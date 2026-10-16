"""Concise Skill instructions shared by the coding-tool entry point."""

import json
from pathlib import Path
import unittest

from aiflame.integrations.cli_tools.skill import NAMES, SKILL_URL, installation_prompt


class CodingSkillTests(unittest.TestCase):
    def test_generic_and_named_instructions_have_only_request_and_repository(self):
        generic = installation_prompt()
        self.assertEqual(generic.splitlines(), [
            '请安装此 Skill，并用它为当前编程工具接入 SeeEnd：', SKILL_URL])
        for source, name in NAMES.items():
            self.assertEqual(installation_prompt(source).splitlines(), [
                f'请安装此 Skill，并用它为{name}接入 SeeEnd：', SKILL_URL])

    def test_optional_scope_is_preserved_without_assuming_default_paths(self):
        prompt = installation_prompt('codex', {'config_dir': '/custom folder'})
        self.assertEqual(prompt.splitlines()[-1], '配置目录：/custom folder')
        prompt = installation_prompt('deepseek-harness', {'profile': 'work'})
        self.assertEqual(prompt.splitlines()[-1], 'Profile: work')
        self.assertNotIn('配置目录', prompt)

    def test_english_instruction_translates_target_and_preserves_url_and_scope(self):
        path = Path(__file__).resolve().parents[1] / 'aiflame/assets/locales/en.json'
        translations = json.loads(path.read_text(encoding="utf-8"))
        prompt = installation_prompt(parameters={'config_dir': '/custom folder'},
                                     translate=lambda value: translations.get(value, value))
        self.assertNotRegex(prompt, r'[\u4e00-\u9fff]')
        self.assertIn('the current coding tool', prompt)
        self.assertIn(SKILL_URL, prompt)
        self.assertIn('/custom folder', prompt)
