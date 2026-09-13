"""Installer contracts: platform routing, transactions, paths and uninstall ownership."""
import argparse
import importlib.util
import json
import pathlib
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
i = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i)


def system(release, env=None, markers=(), commands=(), ro=False):
    return i.detect(release, env or {}, lambda p: p in markers, lambda p: "/usr/bin/" + p if p in commands else None, ro)


def caps(ready=False, compositor="sway", podman=True):
    return {"missing": [] if ready else ["gi"], "commands": {"dbus-daemon": "/usr/bin/dbus-daemon", compositor: "/usr/bin/" + compositor, "podman": "/usr/bin/podman" if podman else None}, "gstreamer": {}}


class Installer(unittest.TestCase):
    def test_distribution_matrix_and_immutable_precedence(self):
        cases = [
            ({"ID": "steamos", "ID_LIKE": "arch"}, (), "pacman", True, "arch"),
            ({"ID": "arch"}, (), "pacman", False, "arch"),
            ({"ID": "ubuntu", "ID_LIKE": "debian"}, (), "apt-get", False, "debian"),
            ({"ID": "debian"}, (), "apt-get", False, "debian"),
            ({"ID": "fedora", "VARIANT_ID": "workstation"}, (), "dnf", False, "fedora"),
            ({"ID": "fedora", "VARIANT_ID": "kinoite"}, (), "dnf", True, "fedora"),
            ({"ID": "bazzite", "ID_LIKE": "fedora"}, (), "dnf", True, "fedora"),
            ({"ID": "bluefin", "ID_LIKE": "fedora"}, (), "dnf", True, "fedora"),
            ({"ID": "opensuse-tumbleweed"}, (), "zypper", False, "suse"),
            ({"ID": "opensuse-aeon", "ID_LIKE": "suse"}, ("/run/ostree-booted",), "zypper", True, "suse"),
            ({"ID": "nixos"}, (), None, True, "unknown"),
            ({"ID": "custom"}, ("/run/ostree-booted",), None, True, "unknown"),
        ]
        for release, markers, manager, atomic, family in cases:
            with self.subTest(release=release):
                result = system(release, markers=markers, commands=[manager] if manager else [])
                self.assertEqual((result["atomic"], result["family"], result["package_manager"]), (atomic, family, manager))
                plan = i.make_plan(result, caps())
                self.assertEqual(plan["runtime"], "container" if atomic or not manager else "native")
                if atomic:
                    self.assertIsNone(i.make_plan(result, caps(), "native")["dependency_command"])

    def test_omarchy_and_session_detection(self):
        result = system({"ID": "arch"}, {"OMARCHY_PATH": "/opt/omarchy", "HYPRLAND_INSTANCE_SIGNATURE": "session", "WAYLAND_DISPLAY": "wayland-1"}, markers=["/opt/omarchy/version"], commands=["pacman"])
        self.assertEqual((result["omarchy"], result["desktop"], result["session"]), (True, "Hyprland", "wayland"))
        self.assertEqual(i.make_plan(result, caps())["dependency_command"][:4], ["sudo", "pacman", "-S", "--needed"])
        self.assertEqual(system({}, {"SWAYSOCK": "/a", "DISPLAY": ":1"})["desktop"], "Sway")

    def test_atomic_native_when_already_complete(self):
        host = system({"ID": "steamos"}, ro=True, commands=["pacman"])
        self.assertEqual(i.make_plan(host, caps(True))["runtime"], "native")
        self.assertIsNone(i.make_plan(host, caps(False), "native")["dependency_command"])

    def test_kwin_incomplete_falls_back_to_sway(self):
        libraries = caps(True)
        libraries["commands"].update({name: "/usr/bin/" + name for name in ("kwin_wayland", "pipewire", "wireplumber")})
        host = system({"ID": "arch"})
        self.assertEqual(i.make_plan(host, libraries)["compositor"], "sway")
        libraries.update(wireplumber_compatible=True, gstreamer={name: True for name in ("pipewiresrc", "appsink", "videoconvert")})
        self.assertEqual(i.make_plan(host, libraries)["compositor"], "kwin")

    def test_os_release_never_executes_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "os-release"
            marker = pathlib.Path(directory) / "executed"
            path.write_text('ID=arch\nPRETTY_NAME="Arch Linux"\nEVIL="$(touch ' + str(marker) + ')"\nBROKEN="unterminated\n')
            result = i.os_release(path)
            self.assertEqual(result["PRETTY_NAME"], "Arch Linux")
            self.assertIn("$(touch", result["EVIL"])
            self.assertFalse(marker.exists())

    def args(self, root):
        return argparse.Namespace(skills_dir=root / "skills with 'quotes'", bin_dir=root / "bin", data_dir=root / "share", update=False, dry_run=False)

    def plan(self):
        return i.make_plan(system({"ID": "arch"}), caps(True))

    def test_update_backup_uninstall_and_shell_quoting(self):
        import subprocess
        with tempfile.TemporaryDirectory(prefix="cul installer ") as directory:
            args = self.args(pathlib.Path(directory))
            result = i.install(args, self.plan())
            launcher = pathlib.Path(result["launcher"])
            run = subprocess.run([str(launcher), "--help"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                i.install(args, self.plan())
            args.update = True
            old_launcher = launcher.read_bytes()
            for _ in range(3):
                update = i.install(args, self.plan())
                self.assertEqual(len(update["backups"]), 1)
                self.assertEqual(list(args.skills_dir.rglob("SKILL.md")), [pathlib.Path(result["skill"])])
                self.assertEqual(update["warnings"], [])
                backup = pathlib.Path(update["backups"][0])
                self.assertNotIn(args.skills_dir, backup.parents)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                with tarfile.open(backup) as archive:
                    self.assertEqual(archive.extractfile("launcher").read(), old_launcher)
                    self.assertEqual(archive.extractfile("skill/SKILL.md").read(), pathlib.Path(result["skill"]).read_bytes())
                    self.assertEqual(json.load(archive.extractfile("backup.json"))["original_paths"]["launcher"], str(launcher))
            args.dry_run = True
            self.assertFalse(i.uninstall(args)["uninstalled"])
            self.assertTrue(launcher.exists())
            args.dry_run = False
            self.assertTrue(i.uninstall(args)["uninstalled"])
            self.assertFalse(launcher.exists())
            self.assertTrue(all(pathlib.Path(p).exists() for p in update["backups"]))

    def test_legacy_backup_migration_preserves_files_modes_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            result = i.install(args, self.plan())
            legacy = args.skills_dir / (i.NAME + ".backup-20260913T204400995977Z")
            shutil.copytree(pathlib.Path(result["skill"]).parent, legacy)
            custom = legacy / "custom.txt"
            custom.write_text("user edits must survive\n")
            custom.chmod(0o640)
            (legacy / "custom-link").symlink_to("custom.txt")
            unrelated = args.skills_dir / (i.NAME + ".backup-manual")
            unrelated.mkdir()
            symlink = args.skills_dir / (i.NAME + ".backup-20260913-123456")
            symlink.symlink_to(unrelated, target_is_directory=True)
            args.update = True
            update = i.install(args, self.plan())
            self.assertFalse(legacy.exists())
            self.assertTrue(unrelated.is_dir())
            self.assertTrue(symlink.is_symlink())
            self.assertEqual(list(args.skills_dir.rglob("SKILL.md")), [pathlib.Path(result["skill"])])
            self.assertEqual(len(update["migrated_backups"]), 1)
            with tarfile.open(update["migrated_backups"][0]["archive"]) as archive:
                self.assertEqual(archive.extractfile("skill/custom.txt").read(), b"user edits must survive\n")
                self.assertEqual(archive.getmember("skill/custom.txt").mode, 0o640)
                self.assertEqual(archive.getmember("skill/custom-link").linkname, "custom.txt")
                self.assertEqual(json.load(archive.extractfile("backup.json"))["original_paths"]["skill"], str(legacy))

    def test_failed_archive_does_not_replace_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            result = i.install(args, self.plan())
            launcher = pathlib.Path(result["launcher"])
            launcher.write_text("original custom launcher\n")
            args.update = True
            with patch.object(i, "backup_archive", side_effect=OSError("archive disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    i.install(args, self.plan())
            self.assertEqual(launcher.read_text(), "original custom launcher\n")
            self.assertEqual(list(args.skills_dir.rglob("SKILL.md")), [pathlib.Path(result["skill"])])

    def test_failed_legacy_archive_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            result = i.install(args, self.plan())
            legacy = args.skills_dir / (i.NAME + ".backup-20260913-123456")
            shutil.copytree(pathlib.Path(result["skill"]).parent, legacy)
            with patch.object(i, "backup_archive", side_effect=OSError("archive disk full")):
                migrated, warnings = i.migrate_backups(legacy, args.data_dir / "backups")
            self.assertEqual(migrated, [])
            self.assertEqual(len(warnings), 1)
            self.assertTrue((legacy / "SKILL.md").is_file())

    def test_backup_storage_cannot_be_inside_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            # Application identity is outside the source, but backup storage
            # would land inside it. Reject before creating either directory.
            args.data_dir = i.ROOT / "skills"
            with self.assertRaisesRegex(RuntimeError, "outside the source"):
                i.install(args, self.plan())

    def test_rollback_restores_all_original_files(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            before = i.install(args, self.plan())
            launcher = pathlib.Path(before["launcher"])
            old = launcher.read_bytes()
            args.update = True
            original = pathlib.Path.rename
            def failing_rename(path, target):
                if path.name.startswith(".cul-stage-") and pathlib.Path(target) == launcher:
                    raise OSError("injected rename failure")
                return original(path, target)
            with patch.object(pathlib.Path, "rename", failing_rename):
                with self.assertRaisesRegex(OSError, "injected"):
                    i.install(args, self.plan())
            self.assertEqual(launcher.read_bytes(), old)
            self.assertTrue(pathlib.Path(before["skill"]).exists())
            self.assertFalse(list(pathlib.Path(directory).rglob("*.backup-*")))
            self.assertFalse(list(pathlib.Path(directory).rglob("*.tar.gz")))
            self.assertFalse(list(pathlib.Path(directory).rglob(".cul-rollback-*")))
            self.assertEqual(list(args.skills_dir.rglob("SKILL.md")), [pathlib.Path(before["skill"])])
            self.assertTrue(i.uninstall(args)["uninstalled"])

    def test_uninstall_refuses_modified_file_without_partial_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            result = i.install(args, self.plan())
            launcher = pathlib.Path(result["launcher"])
            launcher.write_text("#!/bin/sh\necho custom\n")
            with self.assertRaisesRegex(RuntimeError, "modified"):
                i.uninstall(args)
            self.assertTrue(pathlib.Path(result["skill"]).exists())
            self.assertTrue(launcher.exists())

    def test_install_cannot_overwrite_or_recurse_into_source(self):
        args = self.args(pathlib.Path("/tmp/cul-source-guard"))
        args.update = True
        for location in (i.ROOT / "skills", i.ROOT / "skills" / i.NAME / "nested"):
            with self.subTest(location=location):
                args.skills_dir = location
                with self.assertRaisesRegex(RuntimeError, "outside the source"):
                    i.install(args, self.plan())

    def test_container_launcher_uses_self_contained_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            plan = self.plan()
            plan["runtime"] = "container"
            result = i.install(args, plan)
            self.assertIn("scripts/container-run.sh", pathlib.Path(result["launcher"]).read_text())
            self.assertEqual(result["mcp_configuration"]["mcpServers"][i.NAME]["command"], result["launcher"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
