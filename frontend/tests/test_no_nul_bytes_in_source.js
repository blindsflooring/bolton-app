// Run:  node frontend/tests/test_no_nul_bytes_in_source.js
//
// No source file may contain a literal NUL byte.
//
// WHY THIS IS WORTH A TEST RATHER THAN A ONE-OFF CLEANUP. frontend/order-index.js
// carried two, as deliberate collision-proof sentinels:
//
//     const ORDER_INDEX_AREA_UNSET = '<NUL>none'
//     const ORDER_DRILL_SEARCH     = '<NUL>search'
//
// They worked. What they also did was make git classify the file as BINARY,
// and a binary file cannot be merged hunk by hunk — git conflicts on the
// whole thing:
//
//     warning: Cannot merge binary files: frontend/order-index.js
//
// That is how PR #46 ended up conflicting with PR #45 over changes that did
// not overlap at all: one touched the search box, the other touched two print
// functions three hundred lines away. order-index.js is the busiest file in
// this frontend, so that was not a one-off — it was a tax on every future
// pull request touching it. It also quietly broke `grep`, which needs -a to
// read the file at all, and which is how most searching in this repo is done.
//
// The fix keeps the sentinels EXACTLY as they are at runtime and writes them
// as \u0000 escapes instead, which is ordinary ASCII in the source. Section A
// proves that equivalence against the real pre-fix file from git rather than
// asserting it, because "identical at runtime" is the entire claim.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const REPO = path.join(__dirname, '..', '..');
const failures = [];
function check(label, condition, detail) {
  console.log((condition ? '  ok   ' : '  FAIL ') + label + (detail ? '  ' + detail : ''));
  if (!condition) failures.push(label + (detail ? ' -- ' + detail : ''));
}

// The two sentinel declarations, pulled out of whichever version of the file
// we are given and evaluated. Nothing else in order-index.js is executable
// outside a browser, so this reads the constants rather than loading it.
function sentinelsFrom(source) {
  const out = {};
  [['ORDER_INDEX_AREA_UNSET', /const ORDER_INDEX_AREA_UNSET = ('[^']*'|"[^"]*");/],
   ['ORDER_DRILL_SEARCH', /const ORDER_DRILL_SEARCH = ('[^']*'|"[^"]*");/]].forEach(function (pair) {
    const m = source.match(pair[1]);
    if (m) out[pair[0]] = eval(m[1]);   // eslint-disable-line no-eval
  });
  return out;
}

console.log('\n' + '='.repeat(72));
console.log('A. THE SENTINELS DID NOT CHANGE — proved, not asserted');
console.log('='.repeat(72) + '\n');

const current = fs.readFileSync(path.join(REPO, 'frontend', 'order-index.js'), 'utf8');
const now = sentinelsFrom(current);

check('both sentinels are still declared',
  typeof now.ORDER_INDEX_AREA_UNSET === 'string' && typeof now.ORDER_DRILL_SEARCH === 'string');

const NUL = String.fromCharCode(0);
check('the area sentinel is still a NUL followed by "none"',
  now.ORDER_INDEX_AREA_UNSET === NUL + 'none',
  JSON.stringify(now.ORDER_INDEX_AREA_UNSET));
check('the search sentinel is still a NUL followed by "search"',
  now.ORDER_DRILL_SEARCH === NUL + 'search',
  JSON.stringify(now.ORDER_DRILL_SEARCH));
check('and the first character really is U+0000, not a space',
  now.ORDER_INDEX_AREA_UNSET.charCodeAt(0) === 0
  && now.ORDER_DRILL_SEARCH.charCodeAt(0) === 0,
  'charCodes: ' + now.ORDER_INDEX_AREA_UNSET.charCodeAt(0) + ', '
    + now.ORDER_DRILL_SEARCH.charCodeAt(0));
check('lengths are unchanged (5 and 7)',
  now.ORDER_INDEX_AREA_UNSET.length === 5 && now.ORDER_DRILL_SEARCH.length === 7,
  now.ORDER_INDEX_AREA_UNSET.length + ', ' + now.ORDER_DRILL_SEARCH.length);

// The real proof: the version that shipped, straight out of git, compared to
// the version in the working tree. If these differ by so much as one code
// unit, the change was not cosmetic and must not ship.
let previous = null;
try {
  previous = execFileSync('git', ['show', 'origin/main:frontend/order-index.js'],
                          { cwd: REPO, encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
} catch (e) { /* no git, or no origin/main - section falls back below */ }

if (previous === null) {
  console.log('  (skipped the against-git comparison: origin/main not reachable here)');
} else {
  const before = sentinelsFrom(previous);
  console.log('  shipped version had NUL bytes in source : ' + (previous.indexOf(NUL) > -1));
  console.log('  this version has NUL bytes in source    : ' + (current.indexOf(NUL) > -1));
  check('the shipped file really did contain literal NULs (so this test is real)',
    previous.indexOf(NUL) > -1);
  check('THE POINT: the area sentinel is byte-for-byte what it always was',
    before.ORDER_INDEX_AREA_UNSET === now.ORDER_INDEX_AREA_UNSET);
  check('and so is the search sentinel',
    before.ORDER_DRILL_SEARCH === now.ORDER_DRILL_SEARCH);
}

console.log('\n' + '='.repeat(72));
console.log('B. NOTHING ELSE IN THE FILE MOVED');
console.log('='.repeat(72) + '\n');

if (previous !== null) {
  // Every difference must be one of the two sentinel lines. A stray edit
  // riding along in a "cosmetic" change is exactly what nobody would review.
  const a = previous.split('\n');
  const b = current.split('\n');
  check('the file still has the same number of lines', a.length === b.length,
    a.length + ' vs ' + b.length);
  const changed = [];
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    if (a[i] !== b[i]) changed.push(i + 1);
  }
  console.log('  changed lines: ' + (changed.join(', ') || 'none'));
  check('exactly two lines differ', changed.length === 2, changed.join(', '));
  check('and both are sentinel declarations',
    changed.every(function (n) {
      return /const ORDER_(INDEX_AREA_UNSET|DRILL_SEARCH) =/.test(b[n - 1]);
    }), changed.map(function (n) { return b[n - 1].slice(0, 60); }).join(' | '));
}

console.log('\n' + '='.repeat(72));
console.log('C. AND IT CANNOT COME BACK');
console.log('='.repeat(72) + '\n');
console.log('  A cleanup that only cleans up once is a cleanup that gets undone.\n');

function sourceFiles(dir, acc) {
  acc = acc || [];
  fs.readdirSync(dir, { withFileTypes: true }).forEach(function (entry) {
    if (['node_modules', '.git', 'venv', '__pycache__', 'uploads', 'pricelists'].indexOf(entry.name) > -1) return;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full, acc);
    if (/\.(js|css|html|py|md|json)$/.test(entry.name)) acc.push(full);
  });
  return acc;
}

const files = sourceFiles(path.join(REPO, 'frontend'))
  .concat(sourceFiles(path.join(REPO, 'backend')).filter(function (f) {
    return f.indexOf('venv') === -1;
  }));
const offenders = files.filter(function (f) {
  return fs.readFileSync(f).includes(0x00);
});
console.log('  scanned ' + files.length + ' source files');
offenders.forEach(function (f) { console.log('    NUL in ' + path.relative(REPO, f)); });
check('no source file contains a literal NUL byte', offenders.length === 0,
  offenders.map(function (f) { return path.relative(REPO, f); }).join(', '));

console.log('  (use a \\u0000 escape instead — same string at runtime, and git');
console.log('   can still merge the file hunk by hunk)');

console.log('\n' + '='.repeat(72));
if (failures.length) {
  console.log('FAILURES: ' + failures.length);
  failures.forEach(function (f) { console.log('  - ' + f); });
  console.log('='.repeat(72));
  process.exit(1);
}
console.log('ALL CHECKS PASSED');
console.log('');
console.log('The sentinels are the same strings they have always been, and git can');
console.log('merge order-index.js hunk by hunk again.');
console.log('='.repeat(72));
