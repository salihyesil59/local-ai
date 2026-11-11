"""Project profiles of the coding agent: a Gradle mod is verified with Gradle and its library sources can be read."""

import zipfile

from local_ai.coder import navigate as nav
from local_ai.coder import profiles as pf
from local_ai.coder import verify as vf

JAVA = """package com.example;

public final class Sword extends Item {
    private static final int DAMAGE = 7;

    public Sword(Properties properties) {
        super(properties);
    }

    @Override
    public InteractionResult use(Level level, Player player) {
        return helper(level);
    }

    private static <T> List<T> helper(Level level) {
        return List.of();
    }
}
"""


def mod(tmp_path):
    (tmp_path / "gradlew").write_text("", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins { id 'net.neoforged.moddev' }", encoding="utf-8")
    artifacts = tmp_path / "build" / "moddev" / "artifacts"
    artifacts.mkdir(parents=True)
    with zipfile.ZipFile(artifacts / "neoforge-1-sources.jar", "w") as z:
        z.writestr("net/minecraft/world/item/Item.java", "public class Item {\n    public Item(Properties p) {}\n}\n")
    return tmp_path


def test_a_neoforge_mod_is_verified_with_gradle_and_a_plain_gradle_project_is_built(tmp_path):
    assert vf.detect_command(tmp_path) is None
    (tmp_path / "gradlew").write_text("", encoding="utf-8")
    assert pf.detect(tmp_path).name == "gradle" and " build " in vf.detect_command(tmp_path)
    assert pf.detect(mod(tmp_path)).name == "neoforge-mod" and "compileJava test" in vf.detect_command(tmp_path)


def test_library_sources_are_searched_and_read(tmp_path):
    root = mod(tmp_path)
    found = pf.search_sources(root, "public Item(")
    assert found.startswith("1 matches") and "net/minecraft/world/item/Item.java:2:" in found
    assert pf.search_sources(root, "Item", glob="Block.java").startswith("No matches")
    assert pf.read_source(root, "net/minecraft/world/item/Item.java", 2, 2) == "2:     public Item(Properties p) {}"
    assert pf.read_source(root, "Missing.java").startswith("Not found")
    assert pf.source_archives(tmp_path / "build") == []  # no profile there: the tools are not offered


def test_a_mods_resource_files_are_checked_without_the_game(tmp_path):
    root = mod(tmp_path)
    assets = root / "src/main/resources/assets/demo"
    for folder in ("blockstates", "models/block", "models/item", "lang", "textures/block"):
        (assets / folder).mkdir(parents=True)
    (assets / "textures/block/ore.png").write_bytes(b"")
    (assets / "models/block/ore.json").write_text('{"textures": {"all": "demo:block/ore"}}', encoding="utf-8")
    (assets / "models/item/gem.json").write_text('{"textures": {"layer0": "demo:item/gem"}}', encoding="utf-8")
    (assets / "blockstates/ore.json").write_text('{"variants": {"": {"model": "demo:block/oar"}}}', encoding="utf-8")
    (assets / "lang/en_us.json").write_text('{"item.demo.gem": "Gem",}', encoding="utf-8")
    java = root / "src/main/java/Demo.java"
    java.parent.mkdir(parents=True)
    java.write_text('ITEMS.register("gem", x); Component.translatable("demo.msg"); translatable("title.credits")',
                    encoding="utf-8")  # fmt: skip
    errors, _ = pf.check_files(root)
    said = "\n".join(errors)
    assert "model demo:block/oar does not exist" in said and "texture demo:item/gem does not exist" in said
    assert "en_us.json: not valid JSON" in said and "title.credits" not in said
    (assets / "lang/en_us.json").write_text('{"item.demo.gem": "Gem"}', encoding="utf-8")
    errors, warnings = pf.check_files(root)
    assert any("demo.msg" in e for e in errors) and not any("gem" in e and "translation" in e for e in errors)
    assert pf.check_report(*pf.check_files(root)).startswith("3 errors")
    from local_ai.coder import workflow as wf

    passed, result, _ = wf.run_tests({}, root, "echo built", 30)  # the build passes, the resources do not
    assert not passed and "built" in result.output and "demo:block/oar" in result.output


def test_the_last_crash_or_log_errors_are_read(tmp_path):
    root = mod(tmp_path)
    assert pf.read_game_log(root).startswith("No log")
    logs = root / "run/logs"
    logs.mkdir(parents=True)
    (logs / "latest.log").write_text(
        "[Render thread/INFO] fine\n[Render thread/ERROR] Failed to load model\n"
        "java.lang.NullPointerException: x\n\tat com.demo.Ore.tick(Ore.java:9)\n[INFO] later\n",
        encoding="utf-8",
    )
    said = pf.read_game_log(root)
    assert "Failed to load model" in said and "Ore.java:9" in said and "fine" not in said and "later" not in said


def test_a_verify_command_the_model_writes_runs_the_gradle_wrapper_and_a_tool_name_is_not_a_command(tmp_path):
    root = mod(tmp_path)
    for written in ("./gradlew compileJava", "gradlew compileJava", "gradlew.bat compileJava", ".\\gradlew compileJava"):
        assert pf.own_command(root, written) == f"{pf.GRADLE} compileJava"
    assert pf.own_command(root, "check_files") == "" and pf.own_command(root, "echo ok") == "echo ok"
    assert pf.own_command(tmp_path / "build", "./gradlew x") == "./gradlew x"  # no profile: as written


def test_game_tests_and_data_generators_are_offered_when_the_mod_has_them(tmp_path):
    from local_ai.coder.tools import coder_tools

    root = mod(tmp_path)
    assert pf.available_tasks(root) == []
    java = root / "src/main/java/DemoTests.java"
    java.parent.mkdir(parents=True)
    java.write_text("@GameTest public static void health(GameTestHelper h) {}", encoding="utf-8")
    assert [t.tool for t in pf.available_tasks(root)] == ["run_game_tests"]
    tools = coder_tools(root)
    assert {"run_game_tests", "read_game_log", "check_files", "search_sources"} <= set(tools)
    assert "run_datagen" not in tools and "runGameTestServer" in pf.available_tasks(root)[0].command


def test_godot_fabric_and_pygame_projects_are_recognised(tmp_path):
    for name, files in {
        "fabric-mod": {"gradlew": "", "build.gradle": "plugins { id 'fabric-loom' }"},
        "godot": {"project.godot": "config_version=5"},
        "python-game": {"requirements.txt": "pygame==2.6"},
    }.items():
        root = tmp_path / name
        root.mkdir()
        for file, text in files.items():
            (root / file).write_text(text, encoding="utf-8")
        assert pf.detect(root).name == name
    assert "compileall" in vf.detect_command(tmp_path / "python-game")
    (tmp_path / "python-game/test_score.py").write_text("def test_a(): pass", encoding="utf-8")
    assert "pytest" in vf.detect_command(tmp_path / "python-game")
    godot = tmp_path / "godot"
    assert pf.failed_anyway(godot, "SCRIPT ERROR: Parse Error: x") and not pf.failed_anyway(godot, "ok")
    from local_ai.coder import workflow as wf

    assert not wf.run_tests({}, godot, "echo SCRIPT ERROR: bad", 30)[0]


def test_the_outline_of_a_java_file_lists_its_types_and_methods():
    found = [(d.kind, d.name, d.start, d.depth) for d in nav.definitions("Sword.java", JAVA)]
    assert found == [("class", "Sword", 3, 0), ("method", "Sword", 6, 1), ("method", "use", 11, 1),
                     ("method", "helper", 15, 1)]  # fmt: skip


def test_a_game_mod_needs_no_test_with_every_step_unless_asked(tmp_path):
    from local_ai.coder import prompts
    from local_ai.coder import state as st
    from local_ai.coder import workflow as wf

    plain = tmp_path / "plain"
    plain.mkdir()
    assert wf.tests_required(plain) and not wf.tests_required(plain, "optional")
    root = mod(tmp_path)
    assert not wf.tests_required(root) and wf.tests_required(root, "required")
    said = wf.make_plan(root, "More health", ["Raise the health"], ["Health is 50"], verify_command="echo ok")
    assert "Write its tests" not in said and st.load(root)["tests"] is False
    assert "REVIEW" in wf.review_text(st.load(root)) and "find the test" not in wf.review_text(st.load(root))
    assert "does NOT need a test" in prompts.system_prompt(root, tests=False)
    assert "does NOT need a test" not in prompts.system_prompt(root)
    asked = wf.make_plan(root, "More health", ["Raise the health"], ["Health is 50"], "echo ok", tests=True)
    assert "Write its tests" in asked
