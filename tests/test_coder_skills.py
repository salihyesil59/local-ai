from local_ai.coder import prompts
from local_ai.coder import skills as sk
from local_ai.coder.tools import coder_tools

BUILTIN = {"bug-fix", "new-feature", "refactor", "from-paper", "edge-cases", "cli-tool", "scientific-code"}


def test_builtin_skills_are_complete(project):
    skills = sk.load_skills()
    assert set(skills) == BUILTIN
    for skill in skills.values():
        assert skill["description"] and skill["when_to_use"] and len(skill["body"]) > 200, skill["name"]


def test_user_skills_add_and_replace(project):
    folder = sk.st.home() / "skills"
    folder.mkdir()
    (folder / "bug-fix.md").write_text(
        "---\nname: bug-fix\ndescription: mine\nwhen_to_use: always\n---\nDo X.", "utf-8"
    )
    (folder / "extra.md").write_text("No front matter, name from the file.", "utf-8")
    skills = sk.load_skills()
    assert skills["bug-fix"]["body"] == "Do X." and skills["bug-fix"]["description"] == "mine"
    assert skills["extra"]["body"] == "No front matter, name from the file."
    assert set(skills) == BUILTIN | {"extra"}


def test_load_skill_tool_and_prompt(project):
    tools = coder_tools(project)
    assert tools["load_skill"].fn("bug-fix").startswith("Skill bug-fix:\n1. Read the code")
    unknown = tools["load_skill"].fn("nope")
    assert unknown.startswith("Error: unknown skill") and "edge-cases" in unknown
    system = prompts.system_prompt(project, sk.skill_list(sk.load_skills()))
    assert "- from-paper: Implement an algorithm" in system and "call load_skill" in system
    assert "Skills are short procedures" not in prompts.system_prompt(project)  # no list, no section


def test_suggest_picks_the_skill_whose_keywords_match(project):
    skills = sk.load_skills()
    assert sk.suggest(skills, "Numerical integral of the Hubble parameter for a cosmology library") == "scientific-code"
    assert sk.suggest(skills, "Fix the bug: the parser crashes on empty input") == "bug-fix"
    assert sk.suggest(skills, "Add a command line with argparse") == "cli-tool"
    assert sk.suggest(skills, "something unrelated") == "new-feature"
    assert "edge-cases" not in {s for s in skills if skills[s]["keywords"]}  # only for the review
