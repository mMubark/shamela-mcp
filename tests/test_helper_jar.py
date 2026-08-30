"""Where the helper jar is looked for.

The path used to be hard-coded to the repository layout (`<package>/../java`), which
holds for a checkout and for nothing else. An .mcpb bundle installs the package into an
environment of its own while the jar stays inside the bundle, and a plain wheel install
puts the jar inside the package -- both resolved to a path that does not exist, and the
failure surfaced only at the first search, as "engine unavailable".
"""

from __future__ import annotations

from pathlib import Path

from shamela_mcp import bridge


def place(directory: Path) -> Path:
    """Create a stand-in jar and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    jar = directory / bridge.JAR_NAME
    jar.write_bytes(b"PK\x03\x04 not really a jar")
    return jar


class TestFindHelperJar:
    def test_environment_override_wins(self, tmp_path, monkeypatch) -> None:
        jar = place(tmp_path / "elsewhere")
        monkeypatch.setenv("SHAMELA_MCP_JAR", str(jar))
        assert bridge.find_helper_jar() == jar

    def test_unresolved_placeholder_is_not_a_path(self, tmp_path, monkeypatch) -> None:
        """Claude Desktop passes `${user_config.x}` through when a field is empty."""
        monkeypatch.setenv("SHAMELA_MCP_JAR", "${user_config.jar_path}")
        found = bridge.find_helper_jar()
        assert found.name == bridge.JAR_NAME
        assert "${" not in str(found)

    def test_missing_override_falls_through_to_the_real_jar(self, monkeypatch) -> None:
        monkeypatch.setenv("SHAMELA_MCP_JAR", str(Path("D:/nowhere/absent.jar")))
        assert bridge.find_helper_jar().is_file()

    def test_repository_layout_resolves(self, monkeypatch) -> None:
        monkeypatch.delenv("SHAMELA_MCP_JAR", raising=False)
        found = bridge.find_helper_jar()
        assert found.name == bridge.JAR_NAME
        assert found.is_file(), "the committed helper jar should be found in a checkout"

    def test_package_local_jar_is_preferred_over_the_checkout(
        self, tmp_path, monkeypatch
    ) -> None:
        """A packaged install ships the jar inside the package; that copy must win."""
        package = tmp_path / "site-packages" / "shamela_mcp"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        inside = place(package / "java")
        place(tmp_path / "site-packages" / "java")  # a decoy one level up

        monkeypatch.delenv("SHAMELA_MCP_JAR", raising=False)
        monkeypatch.setattr(bridge, "__file__", str(package / "bridge.py"))
        assert bridge.find_helper_jar() == inside

    def test_bundle_layout_resolves(self, tmp_path, monkeypatch) -> None:
        """An .mcpb bundle keeps the jar beside the package, not inside it."""
        bundle = tmp_path / "bundle"
        package = bundle / "shamela_mcp"
        package.mkdir(parents=True)
        jar = place(bundle / "java")

        monkeypatch.delenv("SHAMELA_MCP_JAR", raising=False)
        monkeypatch.setattr(bridge, "__file__", str(package / "bridge.py"))
        assert bridge.find_helper_jar() == jar

    def test_absent_everywhere_names_a_path_worth_showing(self, tmp_path, monkeypatch) -> None:
        package = tmp_path / "empty" / "shamela_mcp"
        package.mkdir(parents=True)
        monkeypatch.delenv("SHAMELA_MCP_JAR", raising=False)
        monkeypatch.setattr(bridge, "__file__", str(package / "bridge.py"))

        found = bridge.find_helper_jar()
        assert not found.is_file()
        # The health tool prints this path, so it must be the conventional location
        # rather than the last candidate that happened to be tried.
        assert found == tmp_path / "empty" / "java" / bridge.JAR_NAME
