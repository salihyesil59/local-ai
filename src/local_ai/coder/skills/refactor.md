---
name: refactor
description: Restructure code without changing what it does
when_to_use: the user asks to clean up, split, rename, simplify or reorganise existing code
keywords: refactor, clean up, simplify, rename, split, düzenle, temizle, restructure
---
1. Read the code to change and its callers (search for each name you will touch). List the behaviours it has
   today, including odd ones.
2. If those behaviours are not covered by tests, step 1 adds tests that pin them down with the CURRENT outputs
   (characterization tests). They must pass before you change anything.
3. Refactor in small steps, one kind of change per step (move, rename, extract, simplify), and call verify after
   each. A failing test now means the refactor changed behaviour: undo that part, do not edit the test.
4. Do not mix in fixes or new features. If you notice a bug, mention it in your final summary and leave it.
5. Keep public names and signatures unless the user asked to change them.
