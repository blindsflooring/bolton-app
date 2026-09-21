// Run:  node frontend/tests/test_order_search_survives.js
//
// Does the Order Index search box survive being typed into?
//
// THE BUG. "The search box closes after typing just 2 letters." It was not
// closing — it was being DELETED. Typing set:
//
//     orderIndexDrillStage = ORDER_DRILL_SEARCH
//
// which switched renderOrderIndexTable() from orderDashboardHtml() to
// orderDrillDownHtml(), and the input lived only in the dashboard. So the
// first debounced keystroke removed the search box from the DOM mid-word.
// Two letters because the 300ms debounce usually fires after the second
// character at normal typing speed; everything typed after that went nowhere.
//
// THE FOCUS RESTORE COULD NEVER HAVE HELPED, and that is the part worth
// keeping a test on. It began:
//
//     const input = document.getElementById('orderSearchInput');
//     if (input && orderIndexSearchTerm && ...)
//
// On this exact path `input` was null, so the guard quietly did nothing while
// reading like a guard that handled the case. A previous session had already
// fixed a real focus race here (Aug 2026, "Full Real-Browser Walkthrough &
// Audit") and left that restore behind; it was correct for the case it was
// written for and blind to this one.
//
// So this test asserts the STRUCTURAL fact the bug was made of — that the
// element the user is typing into still exists after a render — rather than
// asserting that some function was called.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'order-index.js'), 'utf8');

const failures = [];
function check(label, condition, detail) {
  console.log((condition ? '  ok   ' : '  FAIL ') + label + (detail ? '  ' + detail : ''));
  if (!condition) failures.push(label + (detail ? ' -- ' + detail : ''));
}

function grab(start, end) {
  const i = src.indexOf(start);
  if (i < 0) throw new Error('not found: ' + start);
  const j = end ? src.indexOf(end, i) : src.length;
  return src.slice(i, j < 0 ? src.length : j);
}

// Enough of a DOM to render into and type against.
global.orderIndexSearchTerm = '';
global.ORDER_DRILL_SEARCH = ' search';
eval(grab('function orderSearchBoxHtml', '\nfunction orderDrillDownHtml'));

console.log('\n' + '='.repeat(72));
console.log('A. ONE DEFINITION OF THE BOX, RENDERED BY BOTH SCREENS');
console.log('='.repeat(72) + '\n');

const inputTags = src.match(/id="orderSearchInput"/g) || [];
check('the search input is declared exactly once in the source',
  inputTags.length === 1, inputTags.length + ' declarations');

check('the dashboard renders it through the shared helper',
  /\$\{orderSearchBoxHtml\(\)\}/.test(grab('function orderDashboardHtml', 'function orderSearchBoxHtml')));

const drill = grab('function orderDrillDownHtml');
check('THE FIX: the search results screen renders it too',
  /stage === ORDER_DRILL_SEARCH \? orderSearchBoxHtml\(\) : ''/.test(drill),
  'the box would vanish the moment a search took effect');
check('and only on the search screen, not on every tile drill-down',
  drill.indexOf('orderSearchBoxHtml()') === drill.lastIndexOf('orderSearchBoxHtml()'));

console.log('\n' + '='.repeat(72));
console.log('B. THE BUG ITSELF: TYPE, RE-RENDER, IS THE BOX STILL THERE?');
console.log('='.repeat(72) + '\n');
console.log('  Reproduced structurally: render what each screen produces and');
console.log('  look for the element the user is mid-word in.\n');

function hasSearchBox(html) { return html.indexOf('id="orderSearchInput"') > -1; }

// The dashboard, before anyone types.
global.orderIndexSearchTerm = '';
check('the box is on the dashboard to begin with', hasSearchBox(orderSearchBoxHtml()));

// Now simulate what typing does: the stage flips to search. Render the
// drill-down's own conditional exactly as the source writes it.
function drillSearchHtml(stage) {
  return `<span class="back-link"></span>
    ${stage === ORDER_DRILL_SEARCH ? orderSearchBoxHtml() : ''}
    <div class="landing-welcome"><h1>Search</h1></div>`;
}

global.orderIndexSearchTerm = 'vi';
check('after two letters the box SURVIVES the switch to search results',
  hasSearchBox(drillSearchHtml(ORDER_DRILL_SEARCH)),
  'this is the exact failure that was reported');
check('and carries what was typed so far',
  drillSearchHtml(ORDER_DRILL_SEARCH).indexOf('value="vi"') > -1);

['Viljoen', 'J-0019', 'Voelklip', 'a', 'Huis Lettie Theron'].forEach(function (term) {
  global.orderIndexSearchTerm = term;
  const html = drillSearchHtml(ORDER_DRILL_SEARCH);
  check('survives searching ' + JSON.stringify(term),
    hasSearchBox(html) && html.indexOf('value="' + term + '"') > -1);
});

global.orderIndexSearchTerm = 'Smith "Pty" & Co';
check('a quote in the search term cannot break out of the value attribute',
  orderSearchBoxHtml().indexOf('value="Smith &quot;Pty&quot; & Co"') > -1,
  orderSearchBoxHtml().match(/value="[^\n]*"/)[0]);

// A tile drill-down is NOT a search and must not grow a search box.
global.orderIndexSearchTerm = '';
check('clicking a tile still gets a plain drill-down, no box',
  !hasSearchBox(drillSearchHtml('quoted')));

console.log('\n' + '='.repeat(72));
console.log('C. NOT ONE KEYSTROKE IS LOST TO THE RE-RENDER');
console.log('='.repeat(72) + '\n');
console.log('  orderIndexSearchTerm is the DEBOUNCED value and can be 300ms');
console.log('  behind the box. Re-rendering from it alone silently discards');
console.log('  whatever was typed in that window.\n');

const render = grab('function renderOrderIndexTable', 'function orderSearchBoxHtml');
check('the live input is read BEFORE innerHTML destroys it',
  render.indexOf('const liveSearch') < render.indexOf('el.innerHTML'),
  'captured too late to be captured at all');
check('its typed value is captured, not just its focus',
  /value: liveSearch\.value/.test(render));
check('and the caret position with it', /caret: liveSearch\.selectionStart/.test(render));
check('the typed value is restored after the render',
  /input\.value = searchState\.value/.test(render));
check('and the value is restored BEFORE the caret is placed',
  render.indexOf('input.value = searchState.value') < render.indexOf('setSelectionRange'));
check('the caret cannot be placed past the end of the text',
  /Math\.min\(searchState\.caret/.test(render));

console.log('\n' + '='.repeat(72));
console.log('D. AND IT DOES NOT STEAL FOCUS IT NEVER HAD');
console.log('='.repeat(72) + '\n');
console.log('  The desired behaviour says the box closes when you click away or');
console.log('  pick a result. Grabbing focus back on every render would fight');
console.log('  that, and on a phone would reopen the keyboard over the results.\n');

check('focus is only restored if the box actually had it',
  /if \(searchState\.hadFocus\)/.test(render));
check('which is decided before the render, not guessed after',
  /hadFocus: document\.activeElement === liveSearch/.test(render));
check('and nothing is restored at all when there was no box',
  /if \(input && searchState\)/.test(render));

// The old guard keyed off orderIndexSearchTerm, so it also refused to act
// when the term was empty - the moment you cleared the box to start again.
check('the old term-is-truthy condition is gone',
  !/input && orderIndexSearchTerm && document\.activeElement !== input/.test(render),
  'clearing the box would drop focus again');

console.log('\n' + '='.repeat(72));
if (failures.length) {
  console.log('FAILURES: ' + failures.length);
  failures.forEach(function (f) { console.log('  - ' + f); });
  console.log('='.repeat(72));
  process.exit(1);
}
console.log('ALL CHECKS PASSED');
console.log('');
console.log('The box the user is typing into still exists after the search takes');
console.log('effect, keeps every character typed while the screen re-rendered,');
console.log('and does not grab focus back once they have moved on.');
console.log('='.repeat(72));
