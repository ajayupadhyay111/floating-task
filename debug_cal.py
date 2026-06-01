import sys
sys.path.insert(0, r'C:\Users\ajay7\Desktop\developement\FloatTask')
from floating_tasks import *

app = QApplication(sys.argv)

panel = PopupPanel()
panel.move(500, 200)
panel.show()

cv = panel.calendar_view
dialog = cv._day_dialog

# Direct test - show dialog manually with a delay
def test_show():
    print("Testing direct show...")
    tasks = get_db().get_tasks_for_date("2026-05-23")
    print(f"Got {len(tasks)} tasks")
    dialog.title_label.setText("Test")
    dialog.move(200, 200)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    print("visible after show:", dialog.isVisible())
    print("window state:", dialog.windowState())

QTimer.singleShot(1000, test_show)
QTimer.singleShot(8000, app.quit)
sys.exit(app.exec())
