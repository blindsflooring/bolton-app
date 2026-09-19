// Run:  node frontend/tests/test_money_sections.js
//
// The three money sections, checked against figures measured off real
// production data on 19 Sept 2026. The fixture reconstructs production
// stage by stage rather than asserting whatever the code happens to
// produce - a test that derives its expectations from the code under
// test proves only that the code is consistent with itself.
const fs = require('fs');
const path = require('path').join(__dirname, '..', 'order-index.js');
const src = fs.readFileSync(path, 'utf8');

// the handful of globals the section code reads
global.ORDER_PRODUCTS = [
  { key: '__flooring', label: 'Flooring', cats: ['flooring', 'mixed'] },
  { key: '__blinds', label: 'Blinds', cats: ['blinds', 'mixed'] },
];
global.orderLiveQuotes = q => q.filter(x => x.stage !== 'dead');

function grab(startMarker, endMarker) {
  const i = src.indexOf(startMarker);
  if (i < 0) throw new Error('not found: ' + startMarker);
  const j = src.indexOf(endMarker, i);
  return src.slice(i, j);
}

eval(grab('const MONEY_SECTION_STAGES', 'function moneySectionInProduct').replace('const MONEY_SECTION_STAGES', 'global.MONEY_SECTION_STAGES'));
eval(grab('function moneySectionInProduct', '// ===== Turnover'));

const money = n => 'R' + Math.round(n).toLocaleString('en-ZA');

// Production shape, exactly as measured:
//   landed-not-installed : flooring 8 / R83 763 , blinds 11 / R47 782
//   installed-not-paid   : flooring 3 / R13 276 , blinds 0
//   landed this month    : 22 jobs / R434 940
//   landed all time      : 25 jobs / R508 003
// Reconstructed to reproduce production EXACTLY, stage by stage:
//   won all time      25  = flooring 14 (R375 457) + blinds 11 (R132 546)
//   won in September  22  = flooring 11 (R302 394) + blinds 11 (R132 546)
//   accepted/sched    19  = flooring  8 (owed R83 763) + blinds 11 (owed R47 782)
//   awaiting payment   3  = flooring  3 (owed R13 276)
//   closed             3  = flooring  3, owed R0, landed BEFORE September
// so the 3 pre-September flooring jobs are the closed ones: 375 457 - 302 394 = 73 063
const Q = [];
const push = (n, o) => { for (let i = 0; i < n; i++) Q.push(Object.assign({}, o)); };
const SEPT = '2026-09-10T09:00:00';
const AUG  = '2026-08-10T09:00:00';
const septFloorEach = 302394 / 11;   // 11 September flooring jobs: 8 accepted + 3 awaiting

push(8,  { stage: 'accepted',         job_category: 'flooring', amount_outstanding: 83763 / 8,  total_incl_vat: septFloorEach,  accepted_at: SEPT });
push(11, { stage: 'accepted',         job_category: 'blinds',   amount_outstanding: 47782 / 11, total_incl_vat: 132546 / 11,    accepted_at: SEPT });
push(3,  { stage: 'awaiting_payment', job_category: 'flooring', amount_outstanding: 13276 / 3,  total_incl_vat: septFloorEach,  accepted_at: SEPT });
push(3,  { stage: 'closed',           job_category: 'flooring', amount_outstanding: 0,          total_incl_vat: 73063 / 3,      accepted_at: AUG });

const html = moneySectionsHtml(Q, money);
const all = re => [...html.matchAll(re)].map(m => m[1]);

const heads  = all(/ms-tile-head">([^<]+)</g);
const amts   = all(/ms-tile-amount">([^<]+)</g);
const counts = all(/ms-tile-count">([^<]+)</g);
const scopes = all(/ms-tile-scope">([^<]+)</g);
const rules  = all(/ms-tile-rule">([^<]+)</g);

console.log('tiles rendered:', heads.length);
console.log();
for (let i = 0; i < heads.length; i++) {
  console.log('  %s  %s  (%s)', heads[i].padEnd(10), amts[i].padEnd(12), counts[i]);
  console.log('     scope: %s', scopes[i]);
  console.log('     rule : %s', rules[i]);
}
console.log();

let fails = 0;
const check = (ok, msg) => { if (!ok) { fails++; console.log('  FAIL:', msg); } };

check(heads.length === scopes.length, 'not every tile has a scope');
check(heads.length === rules.length, 'not every tile has a counting rule');
check(scopes.every(s => s.trim().length > 0), 'a scope label is empty');
check(rules.every(r => r.trim().length > 0), 'a counting rule label is empty');

const distinctScopes = [...new Set(scopes)];
console.log('distinct scopes (%d):', distinctScopes.length);
distinctScopes.forEach(s => console.log('   ', s));
check(distinctScopes.length === 3, 'expected exactly 3 distinct time scopes');

const distinctRules = [...new Set(rules)];
console.log('distinct rules (%d):', distinctRules.length);
distinctRules.forEach(r => console.log('   ', r));
check(distinctRules.some(r => /once per trade/.test(r)), 'no product tile states the double-count rule');
check(distinctRules.some(r => /once per job/.test(r)), 'no total tile states the single-count rule');

// the figures themselves
check(amts[0] === money(83763), 'landed/flooring outstanding wrong: ' + amts[0]);
check(amts[1] === money(47782), 'landed/blinds outstanding wrong: ' + amts[1]);
check(amts[2] === money(131545), 'landed total outstanding wrong: ' + amts[2]);
check(amts[3] === money(13276), 'installed/flooring outstanding wrong: ' + amts[3]);
check(amts[4] === money(0), 'installed/blinds should be R0: ' + amts[4]);
check(amts[5] === money(13276), 'installed total wrong: ' + amts[5]);
check(amts[6]  === money(302394), 'September flooring wrong: ' + amts[6]);
check(amts[7]  === money(132546), 'September blinds wrong: ' + amts[7]);
check(amts[8]  === money(434940), 'September total wrong: ' + amts[8]);
check(counts[8] === '22 jobs', 'September job count wrong: ' + counts[8]);
check(amts[9]  === money(375457), 'all-time flooring wrong: ' + amts[9]);
check(amts[10] === money(132546), 'all-time blinds wrong: ' + amts[10]);
check(amts[11] === money(508003), 'all-time total wrong: ' + amts[11]);
check(counts[11] === '25 jobs', 'all-time job count wrong: ' + counts[11]);
check(counts[2] === '19 jobs', 'landed-not-installed count wrong: ' + counts[2]);
check(counts[5] === '3 jobs', 'installed-not-paid count wrong: ' + counts[5]);

console.log();
console.log(fails ? fails + ' CHECK(S) FAILED' : 'ALL CHECKS PASSED');
process.exit(fails ? 1 : 0);
