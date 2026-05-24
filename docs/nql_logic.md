CXL
1. Operation prefixes
1
? Query (GET) — reads data, no side effects. Example: ? users
2
+ Create (POST) — inserts a new record. Example: + users { name "Alice" age 30 }
3
~ Update (PATCH) — mutates an existing record by filter or ID. Example: ~ users (id u_001) { name "Alicia" }
4
! Delete (DELETE) — removes a record. Example: ! users (id u_001)
5
The operator is always the first character of a statement. Any line starting with another character is a parse error.
2. Target (table / resource name)
6
The token immediately after the operator is the target. It must be a bare word — no quotes, no special characters. Example: ? users → target is users.
7
A missing target is a parse error. ? alone is invalid.
3. Default fetch depth
8
? users with no selector block returns all scalar fields up to depth 3. Depth 1 = the target row. Depth 2 = one level of nested objects. Depth 3 = two levels in. Beyond depth 3, relation fields are omitted silently.
9
To override depth and fetch everything: ? users {*}. The wildcard * inside a block means "all fields at this level and all nested levels, no depth cap".
10
{*} can also be used on a nested path: ? users { profile {*} } → returns only the profile relation, fetching all of its fields with no depth cap.
4. Selector block { } — field selection
11
A selector block is a space-separated list of field names and sub-blocks inside { }. Example: ? users { name email } → returns only name and email.
12
A nested relation is selected by writing the relation name followed immediately by its own block: { name profile { bio } }. No comma, no colon.
13
Fields at the same level are separated by whitespace only. The closing } of a nested block acts as the separator between it and the next sibling field. Example: { name profile { bio } settings { theme } } — profile { bio } and settings { theme } are siblings.
14
Requesting a field that does not exist on the target returns a parse-time warning and omits that field; it is not a fatal error. The same applies to an unrecognised $keyword inside a filter block — warn and ignore, do not halt.
5. Response structure
15
Line 1 — Header: always a [ ] array with metadata tokens: qid (query ID), cost (integer), took (wall-clock time string).
16
Line 2 — Status: bare tokens ok true or ok false. On failure, additional tokens follow: ok false err "message".
17
Line 3+ — Payload: the target name, then a { } block wrapping all result rows. Each row is its own anonymous { } block inside the outer wrapper.
[ qid "q_abc123" cost 21 took "44ms" ]
ok true
users {
    { name "Alice" age 30 }
    { name "Bob"   age 25 }
}
18
A single-row result still wraps in the outer { }: users { { name "Alice" } }. No special-casing for one result.
19
An empty result returns users { } with no inner blocks.
6. Separators — the { }-as-delimiter system
20
There are no commas anywhere in Piql — not in queries, not in responses.
21
Inside a { } block, key-value pairs are whitespace-separated. The closing } is the record terminator.
22
Two consecutive anonymous blocks at the same nesting level — { ... }{ ... } — are distinct records. The parser tracks brace depth: when depth returns to the level it was at before the first {, one record is complete.
23
Nesting depth rule: { increments depth by 1; } decrements. Records at the same level share the same depth value at their opening brace.
7. Key-value parsing inside { }
24
Inside any { } block, tokens are consumed in pairs: token 1 = key, token 2 = value. A key is always a bare word. A value is the next token (bare word, quoted string, number, keyword, or a { } / [ ] block).
25
If the count of non-block tokens inside a { } is odd (unpaired key), that is a parse error: "odd token count in object block".
26
A nested { } or [ ] that follows a key is the value for that key — it counts as one value unit regardless of how many tokens it contains internally.
8. Type system
27
int — A bare numeric token (digits only, optional leading -): 123, -42, 0.
28
float — A numeric token containing exactly one .: 3.14, -0.5.
29
bool — The bare keywords true and false (lowercase only). Reserved — cannot be used as key names.
30
null — The bare keyword null (lowercase). Reserved. Represents an absent value.
31
string (bare) — Any other bare word that is not a reserved keyword and contains no whitespace: Berlin, light, fan.
32
string (quoted) — Anything inside double quotes "...". Required when the string: (a) contains whitespace, (b) is a number that should stay a string ("22"), (c) is a reserved keyword used as a string value ("true", "null"), or (d) contains special characters.
33
Reserved keywords that must always be quoted if used as string values: true false null *. Using them bare in value position triggers automatic type resolution — they are never treated as strings unless quoted.
34
A key is always a bare word. A key that is a reserved keyword is a parse error — keys cannot be true, false, null, or *.
35
Escape sequences inside quoted strings: \" for a literal quote, \\ for a backslash, \n for newline. No other escape sequences are defined.
9. List values — [ ] blocks
36
A [ ] block is an ordered list of scalar values. There are no key-value pairs inside it — every token is an individual value.
37
Values inside [ ] are whitespace-separated and follow the same type rules as values in { } blocks.
38
Example: readBy [ u_0007 u_0008 ] → key is readBy, value is a list of two string IDs.
39
A [ ] block cannot contain nested { } or [ ] blocks. It is always a flat list. To represent a list of objects, use a { } wrapper containing anonymous { } sub-blocks (see rule 22).
40
An empty list is valid: tags [ ].
41
In responses, [ ] at the very first line is always the response header (rule 15), never a data value. The parser identifies this by its position as the first token on line 1.
10. Nested object lists (list of objects)
42
A field whose value is a list of objects uses a { } block containing one or more anonymous { } sub-blocks:
attachments {
    { name "file_a.txt" type "text/plain" }
    { name "file_b.pdf" type "application/pdf" }
}
43
The parser distinguishes "object list" from "object" by whether the first token after the opening { is another { (object list) or a bare word (single object).
44
An object list with one item is still an object list: attachments { { name "file.txt" } }. It does not collapse to a single object.
11. Filtering — ( ) blocks modified
45
Filters follow the target name (before any selector block) using ( ). Inside a filter block, pairs are key-value, exactly like object blocks. Example: ? users ( role admin ).
46
Multiple filter conditions inside one ( ) are implicitly AND: ( role admin active true ) → role is admin AND active is true.
47
OR conditions use two separate ( ) blocks separated by the keyword or: ? users ( role admin ) or ( role moderator ).
48
Comparison operators inside filters: >, <, >=, <=, !=. Written as a three-token group: age > 18. Default (bare key-value) means equality: role admin = role == admin.
49
A filter on a nested field uses dot notation: ( settings.theme dark ).
50
Filters can be combined with selector blocks. The fixed order is: operator → target → filter → selector. Example: ? users ( active true ) { name email }.
12. Record ID targeting modified — replaces rule 59
51
To address a single record by its primary key, use the bare field name id (no $ prefix) inside a filter block with its value: ? users (id u_001). The key id is treated as a plain equality filter targeting the primary key field.
52
Because ID lookup is just a filter, it composes freely with all other filter keys and $keywords. Example: ? users (id u_001 $limit 1) { * } — fetch user u_001, all fields, result capped at 1.
53
The same applies to mutate and delete: ~ users (id u_001) { name "Alicia" } and ! users (id u_001). The # sigil from earlier versions of this spec is removed and is now a parse error.
13. $keyword directives inside filter blocks new
54
Inside any ( ) filter block, tokens beginning with $ are directive keywords, not field names. They control query behaviour rather than filtering rows. Syntax is identical to normal filter pairs: $keyword value.
55
Directive keywords and normal filter pairs coexist freely inside the same ( ) block. The parser distinguishes them by the leading $. Order within the block does not matter.
56
An unrecognised $keyword is a parse-time warning (not a fatal error) and is ignored — consistent with rule 14 for unknown fields.
57
A $keyword used as a field name (without the $ prefix) is a plain field filter, not a directive. limit 10 filters rows whose limit field equals 10; $limit 10 restricts result size to 10. They are distinct and never confused.
14. Defined $keywords new
58
$limit (int) — Restricts the maximum number of rows returned. Example: ( active true $limit 10 ) returns at most 10 active users. Must be a positive integer. A value of 0 is a parse error.
59
$offset (int) — Skips the first N rows before returning results. Example: ( $limit 20 $offset 40 ) returns 20 rows starting at row 41 (page 3 of 20). Must be a non-negative integer. Cannot be used together with $after in the same filter block — that is a parse error.
60
$after (string) — Cursor-based pagination. Value is an opaque cursor string returned in the response header as next "cursor_token". Example: ( $limit 20 $after "cursor_abc" ). When no next page exists, the response header carries next null. Cannot be used together with $offset — parse error.
61
$sort (field [direction]) — Sorts results by a field. Direction is asc (default if omitted) or desc. Example: ( $sort createdAt desc ). Dot notation for nested fields: ( $sort settings.timezone asc ). The value of $sort is two tokens (field + direction) or one token (field only, direction defaults to asc); the parser consumes them eagerly. Multi-field sort uses multiple $sort pairs: ( $sort lastName asc $sort firstName asc ).
62
$fields (int) — Limits the number of top-level fields returned per row. Useful for previews. Example: ( $fields 3 ) returns only the first 3 scalar fields of each row in definition order. Does not affect nested depth. When a selector block { } is also present, $fields further limits the selector's field list; the selector wins on which fields are chosen, $fields wins on count.
63
Full example combining ID lookup, a directive, and a selector:
? users (id u_001 $limit 1) { * }
→ fetch user u_001, all fields, result capped at 1 row

? posts (authorId u_001 active true $sort createdAt desc $limit 5) { title createdAt }
→ latest 5 active posts by user u_001, returning only title and createdAt

? messages ($limit 20 $after "cursor_xyz" $sort sentAt desc) { * }
→ next page of 20 messages after cursor, newest-first, all fields
64
Directives apply per ( ) block. When OR conditions are used (( ... ) or ( ... )), directives in each block apply independently to that branch's candidate set. The results are merged before being returned to the caller. A directive that should apply to the final merged set should appear in both branches.