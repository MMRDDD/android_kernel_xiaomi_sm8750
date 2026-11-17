#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""通过实际执行 setlocalversion 验证内核版本生成结果。"""

import os
import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("setlocalversion")


class TestSetlocalversion(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = pathlib.Path(self.tempdir.name)
        self.src = self.root / "src"
        self.out = self.root / "out"
        self.src.mkdir()
        (self.out / "include/config").mkdir(parents=True)
        self.write_config("android15-6.6", "8")
        self.write_auto_conf()
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Kernel Test")
        self.run_git("config", "user.email", "kernel-test@example.org")
        (self.src / "source.c").write_text("int kernel_test;\n", encoding="utf-8")
        self.commit_source("initial source")

    def run_git(self, *args):
        return subprocess.run(
            ("git", *args), cwd=self.src, check=True, capture_output=True, text=True
        ).stdout.strip()

    def write_config(self, branch, kmi, src=None):
        src = src or self.src
        (src / "build.config.common").write_text(
            f"BRANCH={branch}\nKMI_GENERATION={kmi}\n", encoding="utf-8"
        )

    def write_auto_conf(self, localversion_auto=True, out=None):
        out = out or self.out
        auto = "y" if localversion_auto else "n"
        (out / "include/config/auto.conf").write_text(
            f"CONFIG_LOCALVERSION=-4k\nCONFIG_LOCALVERSION_AUTO={auto}\n",
            encoding="utf-8",
        )

    def commit_source(self, message):
        self.run_git("add", "build.config.common", "source.c")
        self.run_git("commit", "-q", "-m", message)
        return self.run_git("rev-parse", "HEAD")

    def run_script(self, args=(), overrides=None, src=None, out=None):
        src = src or self.src
        out = out or self.out
        env = os.environ.copy()
        for name in ("BUILD_NUMBER", "KERNEL_BUILD_NUMBER", "LOCALVERSION"):
            env.pop(name, None)
        env["KERNELVERSION"] = "6.6.144"
        env.update(overrides or {})
        return subprocess.run(
            ("/bin/sh", str(SCRIPT), *args, str(src)),
            cwd=out,
            env=env,
            capture_output=True,
            text=True,
        )

    def expected_release(
        self,
        head,
        build_number="ogki551678766",
        kmi="8",
        kernel_version="6.6.144",
        android_release="android15",
    ):
        release = f"{kernel_version}-{android_release}-{kmi}-g{head[:12]}"
        if build_number:
            release += f"-ab{build_number}"
        return release + "-4k"

    def test_android_kmi_build_number_and_page_suffix_follow_stock_order(self):
        head = self.run_git("rev-parse", "HEAD")
        result = self.run_script(overrides={"KERNEL_BUILD_NUMBER": "ogki551678766"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.expected_release(head))

    def test_build_number_is_optional(self):
        head = self.run_git("rev-parse", "HEAD")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.expected_release(head, ""))
        result = self.run_script(overrides={"KERNEL_BUILD_NUMBER": "eng.example"})
        self.assertEqual((result.returncode, result.stdout.strip()),
                         (0, self.expected_release(head, "eng.example")), result.stderr)

    def test_kernel_build_number_is_independent_of_rom_build_number(self):
        head = self.run_git("rev-parse", "HEAD")
        cases = (
            ("unset", "example.rom.1", None),
            ("empty", "example.rom.1", ""),
            ("independent", "example.rom.1", "example.kernel.123"),
            ("different ROM", "example.rom.2", "example.kernel.123"),
        )
        for case, rom_build_number, kernel_build_number in cases:
            with self.subTest(case=case):
                overrides = {"BUILD_NUMBER": rom_build_number}
                if kernel_build_number is not None:
                    overrides["KERNEL_BUILD_NUMBER"] = kernel_build_number
                result = self.run_script(overrides=overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.strip(),
                    self.expected_release(head, kernel_build_number or ""),
                )

    def test_release_uses_branch_and_kmi_from_source_config(self):
        self.write_config("android16-6.12", "9")
        head = self.commit_source("update Android kernel branch")
        result = self.run_script(
            overrides={"KERNEL_BUILD_NUMBER": "ogki551678766", "KERNELVERSION": "6.12.7"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            self.expected_release(
                head,
                kmi="9",
                kernel_version="6.12.7",
                android_release="android16",
            ),
        )

    def test_exact_annotated_tag_keeps_git_hash(self):
        self.run_git("tag", "-a", "v6.6.144", "-m", "kernel tag")
        head = self.run_git("rev-parse", "HEAD")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.expected_release(head, ""))

    def test_release_keeps_git_hash_without_tag_distance(self):
        self.run_git("tag", "-a", "v6.6.144", "-m", "kernel tag")
        (self.src / "source.c").write_text("int kernel_test = 1;\n", encoding="utf-8")
        head = self.commit_source("source after kernel tag")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.expected_release(head, ""))
        self.assertNotIn("-00001-", result.stdout)

    def test_dirty_source_keeps_clean_head_version(self):
        head = self.run_git("rev-parse", "HEAD")
        (self.src / "source.c").write_text("int kernel_test = 1;\n", encoding="utf-8")
        result = self.run_script(overrides={"KERNEL_BUILD_NUMBER": "ogki551678766"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.expected_release(head))

    def test_no_local_omits_page_suffix_and_build_number(self):
        head = self.run_git("rev-parse", "HEAD")
        (self.src / "source.c").write_text("int kernel_test = 1;\n", encoding="utf-8")
        result = self.run_script(("--no-local",), {"KERNEL_BUILD_NUMBER": "ogki551678766"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"6.6.144-g{head[:12]}")
        self.assertNotIn("-dirty", result.stdout)

    def test_precomputed_localversion_is_not_duplicated(self):
        self.write_auto_conf(localversion_auto=False)
        precomputed = "-android15-8-g0123456789ab-abogki551678766"
        (self.out / "localversion").write_text(precomputed, encoding="utf-8")
        result = self.run_script(
            overrides={"LOCALVERSION": "", "KERNEL_BUILD_NUMBER": "ogki551678766"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"6.6.144{precomputed}-4k")

    def test_android_config_without_git_fails(self):
        src = self.root / "src_without_git"
        out = self.root / "out_without_git"
        src.mkdir()
        (out / "include/config").mkdir(parents=True)
        self.write_config("android15-6.6", "8", src)
        self.write_auto_conf(out=out)
        result = self.run_script(src=src, out=out)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")

    def test_invalid_android_metadata_fails(self):
        for branch, kmi in (("lineage-24.0", "8"), ("android15-6.6", "eight")):
            with self.subTest(branch=branch, kmi=kmi):
                self.write_config(branch, kmi)
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
