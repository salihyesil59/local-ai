---
name: neoforge-mod
description: Change or extend a Minecraft mod (NeoForge, Gradle, Java) by following what the mod already does
when_to_use: the project is a Minecraft mod and the task adds or fixes an item, block, mob, recipe, spell or text
keywords: minecraft, mod, neoforge, item, block, blok, mob, entity, recipe, tarif, datapack, lang, texture, gradle
---
1. Find the nearest thing the mod already has (search for a similar item, block or mob) and read how it is
   registered, named and used. Copy that pattern; do not invent another one.
2. Never guess a Minecraft or NeoForge class, method or constructor. Look it up with search_sources (the name) and
   read_source (its file), then write the call exactly as declared there. Versions differ: memory is often wrong.
3. A new game object usually needs all of: its registration (the registry class), its translation key in
   assets/<modid>/lang/en_us.json (and other languages present), and its model, blockstate, texture, loot table or
   recipe JSON where similar objects have them. Search the id of the similar object to see every place.
4. Code that touches rendering, screens or key input belongs to the client package only; never reference client
   classes from common code.
   A texture is a PNG image: read_file and write_file cannot handle it. Make a plain 16x16 placeholder with
   run_python (struct + zlib, a few lines) or point the model at a similar object's texture, and say in the
   summary which PNG the user has to draw.
5. Keep JSON valid (no trailing commas, no comments) and ids in lower_snake_case. check_files finds broken JSON,
   missing models and textures and missing translation keys without starting the game.
6. Call verify: it compiles the mod, runs its unit tests and checks the resource files. Fix the first compiler error
   first; later ones often follow from it.
7. If the mod has GameTests (@GameTest), add one for behaviour that needs a world and call run_game_tests once at
   the end. If it has data generators, change the generator and call run_datagen; never edit src/generated.
8. When the user says the game crashed or something went wrong in it, call read_game_log first and fix the cause
   the crash report or stack trace names. What only shows in the game (looks, sounds, world generation) cannot be verified here: say so
   in the summary so the user tests it in the game.
