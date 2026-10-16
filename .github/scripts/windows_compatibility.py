"""Capture focused compatibility failures before the full release checks."""

import json
from pathlib import Path
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
OUTPUT = ROOT / 'validation/windows-compatibility'
OUTPUT.mkdir(parents=True, exist_ok=True)

from PySide6.QtGui import QFontDatabase, QFontInfo, QFontMetrics
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
metrics = QFontMetrics(app.font())
print(json.dumps({'platform': app.platformName(), 'font_families': len(QFontDatabase.families()),
                  'resolved_font': QFontInfo(app.font()).family(),
                  'narrow_text': metrics.horizontalAdvance('iiii'),
                  'wide_text': metrics.horizontalAdvance('WWWW')}), flush=True)


class Result(unittest.TextTestResult):
    captures = 0

    def capture(self, test, error):
        if error:
            traceback.print_exception(*error)
        widget = getattr(test, 'widget', None)
        if widget is None:
            return
        self.captures += 1
        name = f'{self.captures:02d}-{test._testMethodName}'
        widget.window().grab().save(str(OUTPUT / (name + '.png')))
        from PySide6.QtWidgets import QApplication, QWidget
        report = {'test': test.id(), 'font': QApplication.font().toString(), 'pages': []}
        for index in range(widget.tabs.count()):
            page = widget.tabs.widget(index)
            width = page.viewport().width()
            report['pages'].append({
                'index': index, 'viewport': width,
                'content': page.widget().width(), 'overflow': page.horizontalScrollBar().maximum(),
                'wide_controls': [
                    {'class': type(control).__name__, 'name': control.objectName(),
                     'text': control.text() if callable(getattr(control, 'text', None)) else '',
                     'hint': control.minimumSizeHint().width(), 'width': control.width(),
                     'font': control.font().toString()}
                    for control in page.findChildren(QWidget)
                    if control.isVisible() and control.minimumSizeHint().width() > width - 80
                ],
            })
        (OUTPUT / (name + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.capture(test, error)

    def addSuccess(self, test):
        super().addSuccess(test)
        if test._testMethodName in (
            'test_activity_english_filters_fit_minimum_width_and_results_keep_channel_scope',
            'test_preference_channels_fit_when_wechat_connection_state_changes',
        ):
            self.capture(test, None)

    def addError(self, test, error):
        super().addError(test, error)
        self.capture(test, error)

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error:
            self.capture(test, error)


names = [
    'test_responsive_ui', 'test_task_return_ui',
    'test_ui_recovery.RecoveryTests.test_minimum_english_device_detail_has_no_horizontal_overflow',
    'test_ui_recovery.RecoveryTests.test_activity_english_filters_fit_minimum_width_and_results_keep_channel_scope',
]
if '--native-smoke' in sys.argv:
    names = names[-2:]
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
raise SystemExit(not result.wasSuccessful())
