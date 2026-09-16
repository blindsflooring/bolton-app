# Setting up `ask_bolton_live` — the Sales and Admin connection

This is the second read-only database login. `ask_bolton` (Owner) can already
read the history, the live job tables and nothing else. `ask_bolton_live` is
for **Sales and Admin**, and may read the five live job tables **only** — never
the imported history, never the financial statements.

Run the SQL yourself in the Supabase SQL editor and set the environment
variable yourself in Render. **Claude Code should not be given the password or
the connection string**, and does not need either to verify the result — the
self-check proves the boundary from the outside.

---

## 1. Create the role

Pick a fresh strong password. Do not reuse `ask_bolton`'s, and do not reuse
`DATABASE_URL`'s.

```sql
CREATE ROLE ask_bolton_live LOGIN PASSWORD 'PUT-A-FRESH-STRONG-PASSWORD-HERE';
```

## 2. Give it nothing, then give it exactly five tables

```sql
-- start from nothing
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ask_bolton_live;

-- it still needs to be able to see into the schema
GRANT USAGE ON SCHEMA public TO ask_bolton_live;

-- exactly five tables, SELECT only, nothing else
GRANT SELECT ON quote, quotelineitem, quotepayment, ordersheet, paymentfollowup
  TO ask_bolton_live;
```

Note what is **not** in that list: `historicalyeartotal`, `historicalmonthtotal`
and `financialstatement`. Sales and Admin must not reach any of them, and the
self-check probes for exactly that.

## 3. Add the RLS policies

Privileges alone are not enough — this is the thing that went wrong with the
Owner role. RLS is on, so without a policy the role holds SELECT and still sees
zero rows, and the honest-looking answer is *"there is nothing recorded for that
job"*. Same shape as the existing `ask_read` policy.

```sql
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['quote','quotelineitem','quotepayment',
                           'ordersheet','paymentfollowup']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS ask_live_read ON %I', t);
    EXECUTE format('CREATE POLICY ask_live_read ON %I FOR SELECT TO ask_bolton_live USING (true)', t);
  END LOOP;
END $$;
```

## 4. Verify, before it is wired to anything

**Privileges — must be exactly 5 rows, every one of them SELECT:**

```sql
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'ask_bolton_live'
ORDER BY table_name, privilege_type;
```

**Role attributes — every one of these must be `false` except `rolcanlogin`:**

```sql
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolcanlogin
FROM pg_roles WHERE rolname = 'ask_bolton_live';
```

`rolbypassrls` matters most. If it is `true`, every policy above is decoration
and the role reads whatever it likes.

## 5. Set the connection string in Render

A **new** variable on the backend service. Never reuse `DATABASE_URL`, and never
reuse the `ask_bolton` credentials:

```
ASK_BOLTON_LIVE_DATABASE_URL=postgresql://ask_bolton_live.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
```

**The username is the part that goes wrong.** On the Supabase pooler it must be
`ask_bolton_live.<project-ref>` — the role name, then a dot, then the project
ref. The dashboard pre-fills `postgres.<project-ref>`. Change only the password
in that string and the agent connects as the **postgres superuser**: every
validator test still passes, nothing is blocked, and the whole read-only
guarantee is gone with no visible symptom.

That is why `self_check` reports `connected_as` and `is_superuser`, and why it
fails outright on a superuser whatever the other probes said. Check those two
lines first, every time.

There is deliberately **no fallback**. If this variable is missing or wrong,
Sales and Admin get a plain "not configured" message. The code will not quietly
borrow the Owner connection, because that would hand a Sales question a
connection that reads more than Sales may see.

## 6. Confirm from the outside

Deploy, then run both:

```
GET /ask-bolton/self-check?for_role=sales
GET /ask-bolton/self-check?for_role=admin
```

Expected for both:

| | |
|---|---|
| `connected_as` | `ask_bolton_live` |
| `is_superuser` | **false** |
| `ok` | true |
| read quote / quotelineitem / quotepayment / ordersheet / paymentfollowup | pass, with **non-zero** counts |
| read historicalyeartotal *(must be blocked)* | pass |
| read historicalmonthtotal *(must be blocked)* | pass | 
| read financialstatement *(must be blocked)* | pass |
| UPDATE *(must be blocked)* | pass |

A non-zero count on the five is not cosmetic. A zero there means the policies in
step 3 did not land, and questions will be answered "there is nothing recorded"
rather than failing.

---

## What this does not do

**A rep seeing only their own jobs is still enforced by the validator, not by
the database.** Every Sales query must read `quote` and filter it with a bound
`quote.sales_owner = :sales_owner`; a query that reaches `quotelineitem` or
`quotepayment` without going through `quote` is refused, because those tables
carry no owner of their own. That is layer 2, and it is tested — but it is not
layer 1.

The stronger version is an RLS policy keyed to a session setting, so the
database itself refuses another rep's rows the way it refuses an UPDATE. Worth
doing; not done here.

Admin is deliberately **not** person-scoped. Madri invoices and orders for other
people's jobs and needs to see all of them. Settled decision, not an oversight.
