---
name: new-feature
description: Build a new function, module or feature from a written specification, test first
when_to_use: the user asks for something new (a library, a class, a command, an endpoint) in an empty or existing project
keywords: build, implement, write a, yaz, oluştur, library, kütüphane, feature, class, module
---
1. If the project is not empty, read its layout and one existing test to copy its style and imports.
2. Plan 2-5 steps, each a small slice that can be tested alone (data model, core logic, edge cases, interface).
   Criteria: every requirement and every example of the request, one per item, with the exact expected value.
3. For each step write the tests and the code together. A test states one exact expected value (assert x == 7);
   never a range or an either/or. Put examples from the request into tests literally.
4. Public names, argument order and return types come from the request, not from your taste.
5. When verify fails, decide first which side is wrong by re-reading the request; fix the code unless the test
   contradicts the request.
6. In the review, load the skill edge-cases and add what it lists that applies.
