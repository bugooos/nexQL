# Piql Language Specification v0.2.0

## Overview

Piql (Packed Query Language) is a structured, operator-first query protocol designed for
human-readable, type-safe, bidirectional data communication. It is transport-agnostic and
supports read, write, delete, subscribe, and publish operations over any collection.

---

## Syntax Grammar (EBNF)

```ebnf
document     ::= statement
statement    ::= method target args? payload? projection? directive*
method       ::= '?' | '+' | '~' | '!' | '>>' | '<<'
target       ::= IDENT
args         ::= '(' filter_entry* ')'
filter_entry ::= '$'? IDENT comparator? value
comparator   ::= '>=' | '<=' | '!=' | '>' | '<'
payload      ::= '{' kv_entry* '}'          (* create/update only *)
projection   ::= '{' field_sel* '}'
field_sel    ::= field_name directive* nested?
              |  type_condition
              |  wildcard
nested       ::= '{' field_sel* '}'
type_condition ::= '...' 'on' IDENT '{' field_sel* '}'
wildcard     ::= '*'
kv_entry     ::= IDENT ':'? value
value        ::= STRING | NUMBER | BOOL | NULL | IDENT | variable | array | object
variable     ::= '$' IDENT
array        ::= '[' value* ']'
object       ::= '{' kv_entry* '}'
directive    ::= '@' IDENT args?
IDENT        ::= [A-Za-z_][A-Za-z0-9_.]*
STRING       ::= '"' char* '"'
NUMBER       ::= '-'? DIGIT+ ('.' DIGIT+)?
BOOL         ::= 'true' | 'false'
NULL         ::= 'null'
```

---

## Methods

| Operator | Method      | Description                    |
|----------|-------------|--------------------------------|
| `?`      | `read`      | Fetch one or many records      |
| `+`      | `create`    | Insert a new record            |
| `~`      | `update`    | Patch an existing record       |
| `!`      | `delete`    | Remove a record                |
| `>>`     | `subscribe` | Subscribe to live updates      |
| `<<`     | `publish`   | Publish an event to a channel  |

---

## Directives

| Directive         | Scope          | Description                            |
|-------------------|----------------|----------------------------------------|
| `@auth(role:X)`   | field, query   | Restrict access to role X              |
| `@cache(ttl:N)`   | query          | Cache result for N seconds             |
| `@cost(max:N)`    | field          | Field complexity budget                |
| `@cols`           | query          | Return columnar (array-of-arrays) data |
| `@skip(if:$VAR)`  | field          | Conditionally skip field               |
| `@include(if:V)`  | field          | Conditionally include field            |
| `@rate(max:N per:UNIT)` | query   | Rate-limit subscription events         |

---

## Pagination Directives

| Keyword    | Type    | Description                    |
|------------|---------|--------------------------------|
| `$limit`   | int > 0 | Max records returned           |
| `$offset`  | int ≥ 0 | Skip N records                 |
| `$after`   | string  | Cursor-based pagination        |
| `$sort`    | field [asc|desc] | Sort by field         |
| `$fields`  | int ≥ 0 | Return first N fields only     |

`$offset` and `$after` are mutually exclusive.

---

## Type System

| Type          | Description                       |
|---------------|-----------------------------------|
| `uid`         | Unique identifier (prefix_chars)  |
| `str`         | UTF-8 string                      |
| `int`         | 64-bit signed integer             |
| `float`       | IEEE 754 double                   |
| `bool`        | Boolean true/false                |
| `ts`          | Unix timestamp (int or float)     |
| `[T]`         | Array of type T                   |
| `enum(a|b|c)` | Enumerated string values          |
| `obj`         | Unstructured JSON object          |
| `any`         | Unconstrained type                |

---

## Reserved Keywords

`type`, `schema`, `fragment`, `alias`, `true`, `false`, `null`

---

## Example Queries

```nql
# Read single
? user (id "u_abc123") { name email createdAt }

# Read with filters and pagination
? posts (status published $limit 10 $sort createdAt desc) { id title score }

# Create
+ post { title "Hello" body "World" tags ["a" "b"] authorId "u_abc" } { id createdAt }

# Update
~ post (id "p_xyz") { title "Updated" status published } { id updatedAt }

# Delete a field from a record
~ user (id "u_abc") { !settings profile { !bio } } { id }

# Delete
! post (id "p_xyz") { id }

# Subscribe
>> messages ($channelId "ch_001") { id body authorId createdAt } @rate(max 10 per second)

# Field-level auth
? user (id "u_abc") { name email secret @auth(role admin) }

# Wildcard projection
? user (id "u_abc") { * }

# Inline type conditions
? node (id "n_001") { id ... on User { name email } ... on Post { title score } }
```
