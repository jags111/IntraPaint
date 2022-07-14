from PyQt5.QtWidgets import *
from PyQt5.QtGui import QPainter, QPen
from PyQt5.QtCore import Qt, QObject, QThread, QRect, QPoint, pyqtSignal
from edit_ui.mask_panel import MaskPanel
from edit_ui.image_panel import ImagePanel
from edit_ui.inpainting_panel import InpaintingPanel
from edit_ui.sample_selector import SampleSelector
import PyQt5.QtGui as QtGui
from PIL import Image, ImageFilter
import sys

class MainWindow(QMainWindow):
    """Creates a user interface to simplify repeated inpainting operations on image sections."""

    def __init__(self, width, height, im, doInpaint):
        """
        Parameters:
        -----------
        width : int
            Initial window width in pixels.
        height : int
            Initial window height in pixels
        im : Image (optional)
            Optional initial image to edit.
        doInpaint : function(Image selection, Image mask, string prompt, int batchSize int, batchCount)
            Function used to trigger inpainting on a selected area of the edited image.
        """
        super().__init__()

        self.imagePanel = ImagePanel(im)
        self.maskPanel = MaskPanel(im,
                lambda: self.imagePanel.imageViewer.getSelectedSection(),
                self.imagePanel.imageViewer.onSelection)
        self._draggingDivider = False

        def inpaintAndShowSamples(selection, mask, prompt, batchSize, batchCount):
            self.thread = QThread()
            class InpaintThreadWorker(QObject):
                finished = pyqtSignal()
                imageReady = pyqtSignal(Image.Image, int, int)
                def run(self):
                    def sendImage(img, y, x):
                        self.imageReady.emit(img, y, x)
                        QThread.usleep(10) # Briefly pausing the inpainting thread gives the UI thread a chance to redraw.
                    try:
                        doInpaint(selection, mask, prompt, batchSize, batchCount, sendImage)
                    except Exception as err:
                        print(f'Inpainting failure: {err}')
                        sys.exit()
                    self.finished.emit()
            self.worker = InpaintThreadWorker()

            def closeSampleSelector():
                selector = self.centralWidget.currentWidget()
                if selector is not self.mainWidget:
                    self.centralWidget.setCurrentWidget(self.mainWidget)
                    self.centralWidget.removeWidget(selector)
                    self.update()

            def selectSample(pilImage):
                self.imagePanel.imageViewer.insertIntoSelection(pilImage)
                closeSampleSelector()

            def loadSamplePreview(img, y, x):
                # Inpainting can create subtle changes outside the mask area, which can gradually impact image quality
                # and create annoying lines in larger images. To fix this, apply the mask to the resulting sample, and
                # re-combine it with the original image. In addition, blur the mask slightly to improve image composite
                # quality.
                maskAlpha = mask.convert('L').point( lambda p: 255 if p < 1 else 0 ).filter(ImageFilter.GaussianBlur())
                cleanImage = Image.composite(selection, img, maskAlpha)
                sampleSelector.loadSampleImage(cleanImage, y, x)
                sampleSelector.repaint()

            sampleSelector = SampleSelector(batchSize,
                    batchCount,
                    selection,
                    mask,
                    selectSample,
                    closeSampleSelector)
            self.centralWidget.addWidget(sampleSelector)
            self.centralWidget.setCurrentWidget(sampleSelector)
            sampleSelector.setIsLoading(True)
            self.update()

            self.worker.imageReady.connect(loadSamplePreview)
            self.worker.finished.connect(lambda: sampleSelector.setIsLoading(False))
            self.thread.started.connect(self.worker.run)
            self.thread.finished.connect(self.thread.deleteLater)
            self.worker.finished.connect(self.thread.quit)
            self.worker.finished.connect(self.worker.deleteLater)
            self.thread.start()

        self.inpaintPanel = InpaintingPanel(
                inpaintAndShowSamples,
                lambda: self.imagePanel.imageViewer.getImage(),
                lambda: self.imagePanel.imageViewer.getSelectedSection(),
                lambda: self.maskPanel.getMask())
        self.layout = QVBoxLayout()

        self.imageLayout = QHBoxLayout()
        self.imageLayout.addWidget(self.imagePanel, stretch=255)
        self.imageLayout.addSpacing(30)
        self.imageLayout.addWidget(self.maskPanel, stretch=100)
        self.layout.addLayout(self.imageLayout, stretch=255)

        self.layout.addWidget(self.inpaintPanel, stretch=20)
        self.mainWidget = QWidget(self);
        self.mainWidget.setLayout(self.layout)

        self.centralWidget = QStackedWidget(self);
        self.centralWidget.addWidget(self.mainWidget)
        self.setCentralWidget(self.centralWidget)
        self.centralWidget.setCurrentWidget(self.mainWidget)

    def applyArgs(self, args):
        """Applies optional command line arguments to the UI."""
        if args.text:
            self.inpaintPanel.textPromptBox.setText(args.text)
        if ('init_edit_image' in args) and args.init_edit_image:
            self.imagePanel.loadImage(args.init_edit_image)
            self.imagePanel.fileTextBox.setText(args.init_edit_image)
        if ('num_batches' in args) and args.num_batches:
            self.inpaintPanel.batchCountBox.setValue(args.num_batches)
        if ('batch_size' in args) and args.batch_size:
            self.inpaintPanel.batchSizeBox.setValue(args.batch_size)

    def getMask(self):
        return self.maskPanel.getMask()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.centralWidget.currentWidget() is self.mainWidget:
            painter = QPainter(self)
            color = Qt.green if self._draggingDivider else Qt.black
            size = 4 if self._draggingDivider else 2
            painter.setPen(QPen(color, size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            dividerBox = self._dividerCoords()
            yMid = dividerBox.y() + (dividerBox.height() // 2)
            midLeft = QPoint(dividerBox.x(), yMid)
            midRight = QPoint(dividerBox.right(), yMid)
            arrowWidth = dividerBox.width() // 4
            # Draw arrows:
            painter.drawLine(midLeft, midRight)
            painter.drawLine(midLeft, dividerBox.topLeft() + QPoint(arrowWidth, 0))
            painter.drawLine(midLeft, dividerBox.bottomLeft() + QPoint(arrowWidth, 0))
            painter.drawLine(midRight, dividerBox.topRight() - QPoint(arrowWidth, 0))
            painter.drawLine(midRight, dividerBox.bottomRight() - QPoint(arrowWidth, 0))

    def _dividerCoords(self):
        imageRight = self.imagePanel.x() + self.imagePanel.width()
        maskLeft = self.maskPanel.x()
        width = (maskLeft - imageRight) // 2
        height = width // 2
        x = imageRight + (width // 2)
        y = self.imagePanel.y() + (self.imagePanel.height() // 2) - (height // 2)
        return QRect(x, y, width, height)

    def mousePressEvent(self, event):
        if self.centralWidget.currentWidget() is self.mainWidget and self._dividerCoords().contains(event.pos()):
            self._draggingDivider = True
            self.update()

    def mouseMoveEvent(self, event):
        if event.buttons() and self._draggingDivider:
            x = event.pos().x()
            imgWeight = int(x / self.width() * 300)
            maskWeight = 300 - imgWeight
            self.imageLayout.setStretch(0, imgWeight)
            self.imageLayout.setStretch(2, maskWeight)
            self.update()

    def mouseReleaseEvent(self, event):
        if self._draggingDivider:
            self._draggingDivider = False
            self.update()
