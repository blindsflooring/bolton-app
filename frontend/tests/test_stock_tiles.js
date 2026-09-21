// Run:  node frontend/tests/test_stock_tiles.js
//
// What the stock tiles SAY, which is the part that can hurt somebody.
//
// The backend arithmetic is tested separately (test_material_stock.py). This
// is about rendering, and rendering has one rule that matters more than all
// the layout:
//
//     null is not zero.
//
// A material nobody has counted has no known quantity. If that renders as
// "0 bags" then Home is telling Burgert there is no screed left, on a screen
// built to be glanced at, and somebody either orders screed he already has or
// books work against material that isn't there. The honest reading -- "Not
// counted" -- is the whole reason the backend returns null instead of 0.
//
// The second rule: an absence of information is GREY, not red. Colouring
// "nobody looked yet" as an alarm trains people to ignore the real alarm
// next to it, which is how a warning system stops working.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'stock.js'), 'utf8');

function grab(start, end) {
  const i = src.indexOf(start);
  if (i < 0) throw new Error('not found: ' + start);
  const j = end ? src.indexOf(end, i) : src.length;
  return src.slice(i, j < 0 ? src.length : j);
}

global.API = '';
eval(grab('function stockQty', 'function stockAlertHtml'));
eval(grab('function stockAlertHtml', 'function stockTilesHtml'));
eval(grab('function stockTilesHtml', 'async function loadHomeStock'));

const failures = [];
function check(label, condition, detail) {
  console.log((condition ? '  ok   ' : '  FAIL ') + label + (detail ? '  ' + detail : ''));
  if (!condition) failures.push(label + (detail ? ' -- ' + detail : ''));
}

// Exactly the shape stock_overview() returns.
function material(over) {
  return Object.assign({
    key: 'screed', label: 'Screed', unit_label: 'bags', pack_note: '20 kg bag',
    increment: 0.25, source: 'quote_line_bags',
    on_hand: 28, consumed_since_count: 12, needed: 20, on_order: 0,
    available: 28, short_by: 0, is_short: false, never_counted: false,
    last_counted_on: '2026-09-21', last_counted_by: 'burgert',
    last_counted_qty: 40, last_variance: null, last_variance_flagged: false,
  }, over || {});
}
function overview(materials) {
  return {
    materials: materials,
    short: materials.filter(m => m.is_short),
    never_counted: materials.filter(m => m.never_counted),
    needs_attention: materials.filter(m => m.is_short || m.never_counted).length,
  };
}

console.log('\n' + '='.repeat(72));
console.log('A. NULL IS NOT ZERO');
console.log('='.repeat(72) + '\n');

let html = stockTilesHtml(overview([material({ never_counted: true, on_hand: null, available: null })]));
// The BIG figure specifically -- the one a person reads from across the
// room. "On order 0" further down the tile is a real, known zero and is
// perfectly correct; conflating the two is how this assertion first
// failed against working code.
const bigFigure = (html.match(/stock-tile-figure">([\s\S]*?)<\/div>/) || [])[1] || '';
console.log('  big figure renders as: ' + bigFigure.trim().replace(/\s+/g, ' '));
check('an uncounted material does NOT render a big figure of 0',
  !/^\s*0\s*$/.test(bigFigure.replace(/<[^>]*>/g, '')), bigFigure.trim());
check('it says so in words instead', html.indexOf('Not counted') > -1);
check('and is styled as unknown, not as an alarm',
  html.indexOf('stock-tile-unknown') > -1 && html.indexOf('stock-tile-short') === -1);

check('stockQty renders null as a dash, never as 0', stockQty(null, 'bags') === '—',
  stockQty(null, 'bags'));
check('and undefined the same way', stockQty(undefined, 'bags') === '—');
check('but a REAL zero is a real figure', stockQty(0, 'bags') === '0 bags',
  stockQty(0, 'bags'));

html = stockTilesHtml(overview([material({ on_hand: 0, never_counted: false })]));
check('a counted-and-empty material shows 0, not "Not counted"',
  html.indexOf('Not counted') === -1, 'it claimed nobody had counted');

console.log('\n' + '='.repeat(72));
console.log('B. QUARTERS READ AS QUARTERS');
console.log('='.repeat(72) + '\n');

check('a whole number has no decimal tail', stockQty(3, 'drums') === '3 drums', stockQty(3, 'drums'));
check('a quarter reads as .25', stockQty(1.25, 'drums') === '1.25 drums', stockQty(1.25, 'drums'));
check('a half does not read as 1.50', stockQty(1.5, 'drums') === '1.5 drums', stockQty(1.5, 'drums'));
check('three quarters survives', stockQty(2.75, 'drums') === '2.75 drums', stockQty(2.75, 'drums'));
check('the unit can be suppressed for the big figure', stockQty(2.75, '') === '2.75');

console.log('\n' + '='.repeat(72));
console.log('C. THE WARNING SURFACES ON ITS OWN, AND ONLY WHEN REAL');
console.log('='.repeat(72) + '\n');

check('nothing wrong renders no alert at all',
  stockAlertHtml(overview([material()])) === '', 'an alert appeared with nothing wrong');

const shortM = material({
  key: 'bondite', label: 'Bondite', unit_label: 'drums', pack_note: '25 L drum',
  on_hand: 1.5, needed: 2, on_order: 0, available: 1.5, short_by: 0.5, is_short: true,
});
html = stockAlertHtml(overview([shortM]));
check('a real shortfall raises an alert', html.indexOf('stock-alert-short') > -1);
check('it names the material', html.indexOf('Bondite') > -1);
check('it says how much is on hand', html.indexOf('1.5 drums') > -1);
check('it says what is needed', html.indexOf('2 drums') > -1);
check('it says by how much it is short', html.indexOf('Short by 0.5 drums') > -1, html.slice(0, 0));
check('and says plainly that nothing is on order',
  html.indexOf('none on order') > -1);

const withOrder = stockAlertHtml(overview([Object.assign({}, shortM, {
  on_order: 1, available: 2.5, short_by: 0, is_short: true })]));
check('when stock IS on order, it is shown rather than omitted',
  withOrder.indexOf('on order') > -1 && withOrder.indexOf('none on order') === -1);

html = stockAlertHtml(overview([material({ never_counted: true, on_hand: null, available: null })]));
check('never-counted gets its own, calmer notice', html.indexOf('stock-alert-unknown') > -1);
check('which is NOT the red shortfall style', html.indexOf('stock-alert-short') === -1);
check('and says Bolton will not guess',
  html.indexOf('will not guess') > -1 || html.indexOf('not show a zero') > -1);
check('and offers the way to fix it', html.indexOf('openStockCount()') > -1);

console.log('\n' + '='.repeat(72));
console.log('D. A COUNT THAT DISAGREED STAYS VISIBLE');
console.log('='.repeat(72) + '\n');
console.log('  The brief\'s core requirement: a mismatch is flagged, not silently');
console.log('  accepted. A flag nobody sees after the moment of entry is not one.\n');

html = stockAlertHtml(overview([material({
  last_variance: 12, last_variance_flagged: true, last_counted_qty: 40 })]));
check('a flagged variance surfaces on Home', html.indexOf('stock-alert-drift') > -1);
check('it says what was counted', html.indexOf('40 bags') > -1);
check('and by how much it disagreed', html.indexOf('12 bags') > -1);
check('and in which direction', html.indexOf('more than the jobs can account for') > -1);

html = stockAlertHtml(overview([material({
  last_variance: -3, last_variance_flagged: true, last_counted_qty: 25 })]));
check('a shortfall variance reads as "less"', html.indexOf('less than the jobs') > -1);
check('and the amount is not rendered negative', html.indexOf('-3') === -1, html.slice(0, 0));

console.log('\n' + '='.repeat(72));
console.log('E. ALL FOUR MATERIALS, AND WHAT EACH TILE CARRIES');
console.log('='.repeat(72) + '\n');

const all = overview([
  material({ key: 'screed', label: 'Screed', unit_label: 'bags' }),
  material({ key: 'glue', label: 'Glue (Teck 70/70)', unit_label: 'drums', pack_note: '70 m2 per drum' }),
  material({ key: 'slurry', label: 'Slurry', unit_label: 'drums', pack_note: '30 kg drum' }),
  material({ key: 'bondite', label: 'Bondite', unit_label: 'drums', pack_note: '25 L drum' }),
]);
html = stockTilesHtml(all);
['Screed', 'Glue (Teck 70/70)', 'Slurry', 'Bondite'].forEach(function (label) {
  check('the ' + label + ' tile is rendered', html.indexOf(label) > -1);
});
check('every tile shows what jobs need', (html.match(/Needed/g) || []).length === 4);
check('every tile shows what is on order', (html.match(/On order/g) || []).length === 4);
check('every tile is clickable through to counting it',
  (html.match(/openStockCount\('/g) || []).length === 4);
check('and the section offers the daily count',
  html.indexOf('Count stock') > -1);
check('the glue tile names Teck 70/70, not GRIPiTe',
  html.indexOf('Teck 70/70') > -1 && html.indexOf('GRIP') === -1);

check('no materials at all renders nothing rather than an empty shell',
  stockTilesHtml({ materials: [] }) === '');

console.log('\n' + '='.repeat(72));
console.log('F. THE SCREEN NEVER INVENTS A FIGURE OF ITS OWN');
console.log('='.repeat(72) + '\n');
console.log('  Every number comes off /stock/overview. A second calculation in');
console.log('  the browser is how a tile ends up disagreeing with the screen you');
console.log('  opened to check it.\n');

const submit = grab('async function submitStockCount');
check('the count result shows the SERVER\'s sentence, not a rebuilt one',
  submit.indexOf('result.message') > -1);
check('and does not recompute the variance in the browser',
  !/counted\s*-\s*expected/.test(submit));
check('a rejected count surfaces the server\'s reason',
  submit.indexOf('result.detail') > -1);
check('the entry screen shows what Bolton expects before anything is typed',
  src.indexOf('stock-count-expected') > -1 && src.indexOf('Bolton expects') > -1);
check('Home never waits on the stock fetch',
  /catch \(e\) \{ \/\* best-effort/.test(src));

console.log('\n' + '='.repeat(72));
if (failures.length) {
  console.log('FAILURES: ' + failures.length);
  failures.forEach(function (f) { console.log('  - ' + f); });
  console.log('='.repeat(72));
  process.exit(1);
}
console.log('ALL CHECKS PASSED');
console.log('');
console.log('An uncounted material never renders as empty, a shortfall raises its');
console.log('own warning, and a count that disagreed stays visible afterwards.');
console.log('='.repeat(72));
