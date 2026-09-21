// Run:  node frontend/tests/test_print_filename.js
//
// What a printed document is CALLED.
//
// Print -> Save as PDF takes its default filename from document.title, and
// nothing was ever setting that on purpose. The title was whatever the
// sticky header happened to say, so one quote saved under different names
// depending on which screen it was printed from:
//
//   from Quote Builder   "Quote #291 - Bolt-on"
//   from Client Detail   "Client_ Heleen Cilliers - Bolt-on"
//
// Neither is a convention. The second is setPageTitle('Client: ' + name)
// reaching the filesystem, with the OS substituting "_" for the ":" it will
// not allow, and the app's own " - Bolt-on" suffix riding along. Reproducing
// that "Client_" prefix would make a workaround permanent, so the filename
// now follows what Bolton itself calls the same document in Dropbox
// (dropbox_filename(), main.py): client, job reference, document type.
//
// Note what the ORIGINAL report got right and what it got wrong: the title
// code already appended the client name when there was one, so "Quote #291 -
// Bolt-on" is exactly what that template produces when client_name is empty.
// The missing name was real; "this quote is special" was not. Every quote
// printed from a screen whose title is not the client's name had the same
// problem, which is why this is fixed at the print call and not in the title.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'shared.js'), 'utf8');

function grab(startMarker, endMarker) {
  const i = src.indexOf(startMarker);
  if (i < 0) throw new Error('not found: ' + startMarker);
  const j = src.indexOf(endMarker, i);
  if (j < 0) throw new Error('end not found: ' + endMarker);
  return src.slice(i, j);
}

eval(grab('function printDocFilename', '\n// Moved here during'));

const failures = [];
function check(label, condition, detail) {
  console.log((condition ? '  ok   ' : '  FAIL ') + label + (detail ? '  ' + detail : ''));
  if (!condition) failures.push(label + (detail ? ' -- ' + detail : ''));
}

console.log('\n' + '='.repeat(72));
console.log('A. THE NAME LEADS WITH THE CLIENT');
console.log('='.repeat(72) + '\n');

let name = printDocFilename(
  { client_name: 'Tracey Viljoen', job_number: 'J-0019', id: 19 }, 'Quote');
console.log('  ' + name + '\n');
check('starts with the client, not an internal reference',
  name.indexOf('Tracey Viljoen') === 0, name);
check('carries the job number', name.indexOf('J-0019') > -1);
check('says what kind of document it is', name.indexOf('Quote') > -1);
check('carries no " - Bolt-on" app suffix', name.indexOf('Bolt-on') === -1);
check('and no mangled "Client_" prefix', name.indexOf('Client_') === -1);
check('no extension - the browser appends .pdf itself',
  name.slice(-4) !== '.pdf');

check('an invoice is named an invoice',
  printDocFilename({ client_name: 'Tracey Viljoen', job_number: 'J-0019' }, 'Invoice')
    .indexOf('Invoice') > -1);

console.log('\n' + '='.repeat(72));
console.log('B. THE CASES THAT PRODUCED THE BUG');
console.log('='.repeat(72) + '\n');

// Quote #291 itself: a quote with no job number yet, and the empty
// client_name that made its title read "Quote #291" with nothing after it.
name = printDocFilename({ client_name: '', job_number: null, id: 291 }, 'Quote');
console.log('  no client name at all -> ' + name + '\n');
check('still produces a usable name rather than an empty one',
  name.length > 0, name);
check('falls back to the quote reference', name.indexOf('Q-291') > -1, name);
check('does not leave a dangling separator',
  name.indexOf(' -  - ') === -1 && name.trim() === name && name[0] !== '-', name);

name = printDocFilename({ client_name: 'Heleen Cilliers', job_number: null, id: 288 }, 'Quote');
check('a quote not yet accepted uses Q-<id>, not a blank',
  name === 'Heleen Cilliers - Q-288 - Quote', name);

name = printDocFilename({ client_name: '  Spaced  Out  ', job_number: ' J-0001 ' }, 'Quote');
check('stray whitespace is tidied, not preserved into the filename',
  name === 'Spaced  Out - J-0001 - Quote' || name === 'Spaced Out - J-0001 - Quote', name);

console.log('\n' + '='.repeat(72));
console.log('C. CHARACTERS THE FILESYSTEM WOULD MANGLE');
console.log('='.repeat(72) + '\n');
console.log('  The whole "Client_" mess came from letting the OS substitute for a');
console.log('  character we handed it. So substitute deliberately instead.\n');

name = printDocFilename(
  { client_name: 'Smith: A/B <Pty> Ltd | "Main"?*', job_number: 'J-0001' }, 'Quote');
console.log('  ' + name + '\n');
'\\/:*?"<>|'.split('').forEach(function (ch) {
  check('no ' + JSON.stringify(ch) + ' survives', name.indexOf(ch) === -1, name);
});
check('the readable part of the name survives the substitution',
  name.indexOf('Smith') > -1 && name.indexOf('Pty') > -1 && name.indexOf('Main') > -1, name);

// Accented names must NOT be stripped - they are not a filesystem problem,
// and half this client base has one.
name = printDocFilename({ client_name: 'Hüis Lettie Thérôn', job_number: 'J-0019' }, 'Quote');
check('accented client names are kept exactly as written',
  name.indexOf('Hüis Lettie Thérôn') === 0, name);

console.log('\n' + '='.repeat(72));
console.log('D. THE TITLE IS SET FOR THE DIALOG, THEN PUT BACK');
console.log('='.repeat(72) + '\n');

const printBody = grab('function triggerPrint', 'function printDocFilename');
check('triggerPrint takes a filename', /function triggerPrint\(html, filename\)/.test(printBody));
check('it sets document.title before printing', /document\.title = filename/.test(printBody));
check('it restores the previous title afterwards',
  /previousTitle/.test(printBody) && /document\.title = previousTitle/.test(printBody));
check('it waits for afterprint rather than racing the modal dialog',
  /afterprint/.test(printBody));
check('and has a fallback for browsers that never fire afterprint',
  /setTimeout\(restore/.test(printBody));
check('printing without a filename still works, unchanged',
  /if \(!filename\) \{ window\.print\(\); return; \}/.test(printBody));

// Every caller that prints a real business document must pass one.
check('renderPrintDoc passes the filename through',
  /const \{ html, filename \} = await buildPrintDocHtml/.test(src)
  && /triggerPrint\(html, filename\)/.test(src));
check('buildPrintDocHtml returns one',
  /return \{ html, docLabel, mailtoLink, waLink, clientEmail, filename \}/.test(src));

const orderIndex = fs.readFileSync(path.join(__dirname, '..', 'order-index.js'), 'utf8');
check('the job card print passes one too',
  /const \{ html, filename \} = await buildJobCardPrintHtml/.test(orderIndex));
check('and the order sheet print',
  /const \{ html, filename \} = await buildOrderSheetPrintHtml/.test(orderIndex));
check('an order sheet names itself by supplier and order number, not a client',
  /sheet\.supplier_name, sheet\.order_number, 'Order Sheet'/.test(orderIndex));

console.log('\n' + '='.repeat(72));
if (failures.length) {
  console.log('FAILURES: ' + failures.length);
  failures.forEach(function (f) { console.log('  - ' + f); });
  console.log('='.repeat(72));
  process.exit(1);
}
console.log('ALL CHECKS PASSED');
console.log('');
console.log('A printed document and its archived copy now agree on what the');
console.log('document is called, from every screen it can be printed from.');
console.log('='.repeat(72));
