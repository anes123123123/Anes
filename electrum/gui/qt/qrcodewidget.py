import qrcode

from PyQt5.QtGui import QColor, QPen
import PyQt5.QtGui as QtGui
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QTextEdit, QHBoxLayout, QPushButton, QWidget,
    QFileDialog, QLabel
)

from electrum.i18n import _

from .util import WindowModalDialog, get_parent_main_window


class QRCodeWidget(QWidget):

    def __init__(self, data=None, fixedSize=False):
        QWidget.__init__(self)
        self.data = None
        self.qr = None
        self.fixedSize = fixedSize
        if fixedSize:
            self.setFixedSize(fixedSize, fixedSize)
        self.setData(data)

    def setData(self, data):
        if self.data != data:
            self.data = data
        if self.data:
            self.qr = qrcode.QRCode(
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=0,
            )
            self.qr.add_data(self.data)
            if not self.fixedSize:
                k = len(self.qr.get_matrix())
                self.setMinimumSize(k * 5, k * 5)
        else:
            self.qr = None

        self.update()

    def paintEvent(self, e):
        if not self.data:
            return

        black = QColor(0, 0, 0, 255)
        white = QColor(255, 255, 255, 255)
        black_pen = QPen(black)
        black_pen.setJoinStyle(Qt.MiterJoin)

        if not self.qr:
            qp = QtGui.QPainter()
            qp.begin(self)
            qp.setBrush(white)
            qp.setPen(white)
            r = qp.viewport()
            qp.drawRect(0, 0, r.width(), r.height())
            qp.end()
            return

        matrix = self.qr.get_matrix()
        k = len(matrix)
        qp = QtGui.QPainter()
        qp.begin(self)
        r = qp.viewport()

        margin = 10
        framesize = min(r.width(), r.height())
        boxsize = int((framesize - 2 * margin) / k)
        size = k * boxsize
        left = (framesize - size) // 2 + 1
        top = (framesize - size) // 2 + 1
        # Draw white background with margin
        qp.setBrush(white)
        qp.setPen(white)
        qp.drawRect(0, 0, framesize, framesize)
        # Draw qr code
        qp.setBrush(black)
        qp.setPen(black_pen)
        for r in range(k):
            for c in range(k):
                if matrix[r][c]:
                    qp.drawRect(left + c * boxsize, top + r * boxsize, boxsize - 1, boxsize - 1)
        qp.end()


class QRDialog(WindowModalDialog):

    def __init__(self, data, parent=None, title="", show_text=False, description=None):
        WindowModalDialog.__init__(self, parent, title)

        vbox = QVBoxLayout()
        qrw = QRCodeWidget(data)

        if description:
            label = QLabel(description)
            label.setWordWrap(True)
            hbox2 = QHBoxLayout()
            hbox2.addWidget(label)
            vbox.addLayout(hbox2)

        vbox.addWidget(qrw, 1)
        if show_text:
            text = QTextEdit()
            text.setText(data)
            text.setReadOnly(True)
            vbox.addWidget(text)
        hbox = QHBoxLayout()
        hbox.addStretch(1)

        def print_qr():
            main_window = get_parent_main_window(self)
            if main_window:
                filename = main_window.getSaveFileName(_("Select where to save file"), "qrcode.png")
            else:
                filename, __ = QFileDialog.getSaveFileName(self, _("Select where to save file"), "qrcode.png")
            if not filename:
                return
            p = qrw.grab()  # FIXME also grabs neutral colored padding
            p.save(filename, 'png')
            self.show_message(_("QR code saved to file") + " " + filename)

        def copy_to_clipboard():
            p = qrw.grab()
            QApplication.clipboard().setPixmap(p)
            self.show_message(_("QR code copied to clipboard"))

        b = QPushButton(_("Copy"))
        hbox.addWidget(b)
        b.clicked.connect(copy_to_clipboard)

        b = QPushButton(_("Save"))
        hbox.addWidget(b)
        b.clicked.connect(print_qr)

        b = QPushButton(_("Close"))
        hbox.addWidget(b)
        b.clicked.connect(self.accept)
        b.setDefault(True)

        vbox.addLayout(hbox)

        self.setLayout(vbox)
