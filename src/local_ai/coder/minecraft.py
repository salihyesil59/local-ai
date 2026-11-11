"""Checks of a Minecraft mod's resource files that the Java compiler does not see: broken JSON, a model or texture
that a blockstate or model names but that is missing, and translation keys that the code uses but no language
file has. Most mistakes in a mod are there, and the game only shows them as a pink-black box or a raw key."""

from __future__ import annotations

import json
import re
from pathlib import Path

RESOURCES = ("src/main/resources", "src/generated/resources")
TRANSLATABLE = re.compile(r"""translatable\(\s*"([^"\s]+)"\s*[,)]""")
REGISTERED = re.compile(r"""\b(ITEMS|BLOCKS|ENTITY_TYPES|ENTITIES)\.register\w*\(\s*"(\w+)"\s*,""")
KIND = {"ITEMS": "item", "BLOCKS": "block", "ENTITY_TYPES": "entity", "ENTITIES": "entity"}


def _roots(root: Path) -> list[Path]:
    """The resource folders of the mod and of its modules (core/src/main/resources ...)."""
    return [d for r in RESOURCES for d in [root / r, *root.glob("*/" + r)] if d.is_dir()]


def _exists(roots: list[Path], *parts: str) -> bool:
    return any((r.joinpath(*parts)).is_file() for r in roots)


def _refs(value, found: list[str]) -> None:
    """Every string in a JSON value (models name others in nested objects and lists)."""
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _refs(v, found)
    elif isinstance(value, list):
        for v in value:
            _refs(v, found)


def check(root: Path) -> tuple[list[str], list[str]]:
    """(errors, warnings) of the mod's resources. Errors break the game's look or text; warnings are likely gaps."""
    roots = _roots(root)
    errors: list[str] = []
    warnings: list[str] = []
    if not roots:
        return errors, warnings
    spaces = {d.name for r in roots for d in (r / "assets").glob("*") if d.is_dir()} - {"minecraft"}
    docs: dict[Path, object] = {}
    for r in roots:
        for path in r.rglob("*.json"):
            rel = path.relative_to(root).as_posix()
            try:
                docs[path] = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                errors.append(f"{rel}: not valid JSON ({e})")

    def missing(ref: str, folder: str, suffix: str) -> bool:
        space, _, name = ref.partition(":")
        return bool(name) and space in spaces and not _exists(roots, "assets", space, folder, name + suffix)

    for path, doc in docs.items():
        parts = path.relative_to(root).parts
        if "assets" not in parts or len(parts) < parts.index("assets") + 3:
            continue
        rel, kind = path.relative_to(root).as_posix(), parts[parts.index("assets") + 2]
        if kind == "blockstates":
            refs: list[str] = []
            _refs(doc, refs)
            for ref in refs:
                if ":" in ref and "/" in ref and missing(ref, "models", ".json"):
                    errors.append(f"{rel}: model {ref} does not exist")
        elif kind == "models" and isinstance(doc, dict):
            parent = doc.get("parent", "")
            if isinstance(parent, str) and missing(parent, "models", ".json"):
                errors.append(f"{rel}: parent model {parent} does not exist")
            textures: list[str] = []
            _refs(doc.get("textures", {}), textures)
            for ref in textures:
                if not ref.startswith("#") and missing(ref, "textures", ".png"):
                    errors.append(f"{rel}: texture {ref} does not exist")

    langs = {}
    for space in spaces:
        for r in roots:
            for path in (r / "assets" / space / "lang").glob("*.json"):
                if isinstance(docs.get(path), dict):
                    langs.setdefault(path.stem, {}).update(docs[path])
    english = langs.get("en_us", {})
    used: dict[str, str] = {}
    registered: dict[tuple[str, str], str] = {}
    from local_ai.coder.profiles import src_dirs

    for path in (p for d in src_dirs(root) for p in d.rglob("*.java")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        for key in TRANSLATABLE.findall(text):
            if spaces & set(key.split(".")):  # the mod's own key; the game's (title.credits) are in the game
                used.setdefault(key, rel)
        for registry, name in REGISTERED.findall(text):  # a mod set has several ids: any of them may hold it
            registered.setdefault((KIND[registry], name), rel)
            if registry == "ITEMS" and not any(
                _exists(roots, "assets", s, "models", "item", name + ".json") for s in spaces
            ):
                warnings.append(f"{rel}: item {name} has no models/item/{name}.json")
    if english:
        for key, where in used.items():
            if key not in english:
                errors.append(f"{where}: translation key {key} is not in en_us.json")
        for (kind, name), where in registered.items():
            if not any(f"{kind}.{s}.{name}" in english for s in spaces):
                warnings.append(f"{where}: {kind} {name} has no name in en_us.json ({kind}.<modid>.{name})")
        for lang, keys in langs.items():
            gaps = [k for k in english if k not in keys]
            if lang != "en_us" and gaps:
                warnings.append(f"{lang}.json lacks {len(gaps)} keys of en_us.json, e.g. {', '.join(gaps[:3])}")
    return errors, warnings
