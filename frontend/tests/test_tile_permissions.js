// Run:  node frontend/tests/test_tile_permissions.js
//
// Who can see which tile. Business Overview became Owner-only in Sept
// 2026; this asserts it stayed that way, and that nothing else moved
// with it - a role list is the kind of thing a later change widens by
// accident while fixing something adjacent.
//
// The tile list is only half the boundary. /analytics/overview is gated
// with require_owner server-side, because hiding a tile is not a
// permission.
const fs = require('fs');
const src = fs.readFileSync(require('path').join(__dirname, '..', 'shared.js'), 'utf8');

function grab(name) {
  const i = src.indexOf('const ' + name + ' = [');
  const j = src.indexOf('];', i);
  return eval(src.slice(i + ('const ' + name + ' =').length, j + 1));
}

const SALES = grab('SALES_HIDDEN_TILES');
const OWNER = grab('OWNER_ONLY_TILES');
const TT = grab('TRUSTED_TESTER_HIDDEN_TILES');

const can = (role, id) => {
  if (OWNER.includes(id) && role !== 'owner') return false;
  if (role === 'sales' && SALES.includes(id)) return false;
  if (role === 'trusted_tester' && TT.includes(id)) return false;
  return true;
};

console.log('OWNER_ONLY_TILES  :', JSON.stringify(OWNER));
console.log('SALES_HIDDEN_TILES:', JSON.stringify(SALES));
console.log('');
console.log('Business Overview tile AND the Home money sections:');
['owner', 'admin', 'sales', 'trusted_tester'].forEach(r =>
  console.log('   ' + r.padEnd(16) + (can(r, 'business') ? 'VISIBLE' : 'hidden')));
console.log('');
console.log('sanity - nothing else moved:');
['orders', 'clients', 'leads', 'todos', 'hr', 'settings', 'financialRecords'].forEach(id =>
  console.log('   ' + id.padEnd(18) +
    'owner=' + String(can('owner', id)).padEnd(6) +
    'admin=' + String(can('admin', id)).padEnd(6) +
    'sales=' + can('sales', id)));

let fails = 0;
const check = (ok, m) => { if (!ok) { fails++; console.log('  FAIL: ' + m); } };
console.log('');
check(can('owner', 'business') === true, 'owner lost Business Overview');
check(can('admin', 'business') === false, 'admin can still see Business Overview');
check(can('sales', 'business') === false, 'sales can still see Business Overview');
check(can('trusted_tester', 'business') === false, 'trusted tester can still see it');
check(can('admin', 'orders') === true, 'admin lost the Order Index');
check(can('admin', 'hr') === true, 'admin lost HR');
check(can('sales', 'orders') === true, 'sales lost the Order Index');
console.log(fails ? fails + ' CHECK(S) FAILED' : 'ALL CHECKS PASSED');
process.exit(fails ? 1 : 0);
