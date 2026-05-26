import sys
from PyQt6 import QtWidgets
from recoder_gui import App

def main():
    """
    Initialize the PyQt application and start the main UI event loop.
    """
    app = QtWidgets.QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
