# Instructions given to the second annotator

The annotator worked alone, without the paper, our labels or any discussion of individual items, and returned one label per item.
Both kits used GitLab at commit 9d4cbde6 and Sentry at commit 13ccaecc (paths relative to `src/sentry/`).
The two databases or silos of an item share no distributed transaction and no global snapshot, so a reader may see one side written and the other not.

## Templates (97 items, `templates.csv`)

Each item described a cross-database write template: what it does, the rows it writes on each database, the constraints between these rows, and its code locations (the columns of `../out/templates.csv` without our label and required order).
Assuming that the writes on the two databases commit separately, in an order of the annotator's choice, the label is:

| label | meaning |
|---|---|
| `order` | some commit order keeps the listed constraints satisfied after every commit |
| `atomic` | no such order exists: whichever side commits first, some intermediate state violates a constraint |
| `serial` | a constraint relies on data read from the other database that a concurrent update may change |
| `none` | the listed constraints relate no rows on different databases, so every order works |

Two worked examples outside the table: creating a group and its first project under a foreign key (`order`, group first), and renaming a group whose projects cache its path (`atomic`).

## Read-path sites (181 items, `sites.csv`)

Each item marked one line of code and named a foreign key whose child row lies on one database and whose parent row on another; both applications delete the parent first and leave the children to a background task.
The annotator saw only the code around the marked line, and first decided whether the code obtains a child row and then reads its parent by the child's foreign-key column, and then what happens if the parent is gone:

| label | meaning |
|---|---|
| `FP` | not such a read: the parent's id comes from a request or task argument, the object is another model with an attribute of the same name, the code starts from the parent, it is the write path that creates the child, or the code never reaches this foreign key |
| `T` | such a read that handles a missing parent: a nil check followed by a return or skip, safe navigation, a presence check, a permission check that denies on nil, a batch fetch from which the parent is simply absent, or a nil that is passed on and accepted downstream |
| `E` | such a read that fails when the parent is missing: a method call on nil, an assertion, an exception, or a failing request or job |

Two worked examples outside the table: a task argument leads to a member and then to `user_service.get_user(member.user_id)` followed by a nil check (`T`), and a URL parameter leads to a project and then to its pipelines (`FP`).
