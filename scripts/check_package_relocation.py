# SPDX-License-Identifier: AGPL-3.0-only
"""Verify a relocated native bundle without claiming clean-system installation.

检查迁移后的原生包，不将本机构建验证等同干净系统安装验收。
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import tempfile

from package_desktop import verify_desktop, verify_packaged_engines
from prepare_runtime import VERSIONS, verify


class PackageRelocationCheck:
    """Reuse package checks at an actual Unicode/space-containing prefix.

在真正含中文和空格的新前缀下复用验证，并始终尝试恢复原位置。
"""
    def __init__(self, artifact, evidence, source_commit=None):
        self.artifact = artifact.absolute()
        self.evidence = evidence.absolute()
        self.source_commit = source_commit
        if source_commit is not None and not re.fullmatch(
                r'[0-9a-fA-F]{40}', source_commit):
            raise ValueError('Source commit must contain exactly 40 hexadecimal digits')
        if self.artifact.is_symlink() or not self.artifact.is_dir():
            raise ValueError('Artifact must be an existing ordinary bundle directory')
        if os.path.lexists(self.evidence):
            raise ValueError('Evidence destination already exists')
        if self.evidence == self.artifact or self.artifact in self.evidence.parents:
            raise ValueError('Evidence must be outside the bundle')

    @staticmethod
    def write_evidence(path, evidence):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode='w', encoding='utf-8', dir=path.parent,
                    prefix='.package-evidence-', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(evidence, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            # Publish complete evidence exclusively; never replace an existing file.
            # 只发布完整证据，排他创建以避免覆盖已有文件。
            os.link(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink()

    def run(self):
        # A sibling location keeps the move on the same filesystem.
        # 同级临时目录保证整包改名前后处于同一文件系统。
        temporary = Path(tempfile.mkdtemp(
            prefix='polyscholar-relocation-', dir=self.artifact.parent))
        prefix = temporary / '迁移 检查'
        prefix.mkdir()
        moved = prefix / self.artifact.name
        relocated = False
        try:
            if os.path.lexists(moved):
                raise ValueError('Relocation destination already exists')
            self.artifact.rename(moved)
            relocated = True
            verify_desktop(moved)
            verify_packaged_engines(moved)
            resources = (
                moved / 'Contents/Resources/resources'
                if platform.system() == 'Darwin'
                else moved / '_internal/resources'
            )
            with tempfile.TemporaryDirectory(
                    prefix='polyscholar-relocated-runtime-check-') as work:
                for engine in ('babeldoc', 'pdfmathtranslate'):
                    verify(resources / 'runtime' / engine, engine, Path(work))
        finally:
            if relocated:
                if os.path.lexists(self.artifact):
                    raise RuntimeError('Original artifact location is occupied; moved bundle retained')
                moved.rename(self.artifact)
            # Never recursively delete a bundle after a failed restoration.
            # 恢复失败时保留整包，不能递归清除临时检查目录。
            if not any(prefix.iterdir()):
                prefix.rmdir()
                temporary.rmdir()

        evidence = {
            'platform': platform.system(),
            'machine': platform.machine(),
            'sourceCommit': (
                self.source_commit.lower() if self.source_commit else None
            ),
            'verified': {
                'wholeBundleRelocated': True,
                'unicodeAndSpacePrefix': True,
                'originalLocationRestored': True,
                'frozenGuiStartup': True,
                'offlineSyntheticPdfImportAndParse': True,
                'offlineBibTeXRisCslJsonImport': True,
                'localImportPersistenceAfterReopen': True,
                'isolatedPdfValidationAndChildCleanup': True,
                'bundledEngineMemoryConfiguration': True,
                'embeddedRuntimeChecks': {
                    engine: {
                        'pythonMajorMinor': '3.12',
                        'package': VERSIONS[engine][0],
                        'expectedVersion': VERSIONS[engine][1],
                        'versionCommand': True,
                        'pipCheck': True,
                    }
                    for engine in ('babeldoc', 'pdfmathtranslate')
                },
            },
            'notVerified': {
                'cleanMachineInstalled': True,
                'signing': True,
                'realTranslation': True,
                'realEngineCrashRecovery': True,
                'realZoteroContentMigration': True,
            },
        }
        self.write_evidence(self.evidence, evidence)
        print('Package relocation checks passed', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--source-commit')
    args = parser.parse_args()
    PackageRelocationCheck(
        args.artifact, args.evidence, args.source_commit).run()


if __name__ == '__main__':
    main()
