import time
from functools import partial
import os
import sys

import numpy as np
from astropy.io import fits
import datetime
# from astropy import wcs, utils
# import astropy.units as u
# from astropy.stats import gaussian_sigma_to_fwhm
# from astropy.modeling import models, fitting
# import PIL.Image as PILimage
# from PyQt5.QtWidgets import QDesktopWidget

from ginga import cmap
from ginga.misc import log
from ginga.qtw.QtHelp import QtGui, QtCore
from ginga.qtw.ImageViewQt import CanvasView
from ginga.util import plots
# from ginga.util.io import io_fits
from ginga.util.loader import load_data
# from ginga.AstroImage import AstroImage
from ginga.gw import Plot, Widgets

class FileWriter(Widgets.Box):
    def __init__(self, logger, points):
        super(FileWriter, self).__init__()

        vbox = Widgets.VBox()
        text = Widgets.Label("Is this the target or comparison?", halign="center")
        vbox.add_widget(text)
        button_hbox = Widgets.HBox()
        self.targetbtn = Widgets.Button("Target")
        self.targetbtn.add_callback('activated', self.write_target)
        button_hbox.add_widget(self.targetbtn)
        self.comparisonbtn = Widgets.Button("Comparison")
        self.comparisonbtn.add_callback('activated', self.write_comparison)
        button_hbox.add_widget(self.comparisonbtn)
        self.closebtn = Widgets.Button("Close")
        self.closebtn.add_callback('activated', self.dismiss)
        button_hbox.add_widget(self.closebtn)
        vbox.add_widget(button_hbox)
        self.add_widget(vbox)

        self.points = points

    def write_target(self, e):
        try:
            np.savetxt("targetprofile.csv", self.points, delimiter=",", fmt='%f')
        except Exception as e:
            print(e)
        self.delete()
    
    def write_comparison(self, e):
        try:
            np.savetxt("comparisonprofile.csv", self.points, delimiter=",", fmt='%f')
        except Exception as e:
            print(e)
        self.delete()


    def dismiss(self, event):
        self.delete()



class Cuts(Widgets.Box):

    def __init__(self, logger, fitsimage, bm):
        super(Cuts, self).__init__(fitsimage)

        bm.reset_mode(fitsimage)

        self.logger = logger

        self.layertag = 'cuts-canvas'
        self._new_cut = 'New Cut'
        self.cutstag = self._new_cut
        self.tags = [self._new_cut]
        # get Cuts preferences
        self.fitsimage = fitsimage

        self.dc = self.fitsimage.get_canvas().get_draw_classes()
        canvas = self.dc.DrawingCanvas()
        canvas.enable_draw(True)
        canvas.enable_edit(True)
        canvas.set_drawtype('line', color='cyan', linestyle='dash')
        canvas.set_callback('draw-event', self.draw_cb)
        canvas.add_draw_mode('move', down=self.buttondown_cb,
                             move=self.motion_cb, up=self.buttonup_cb,
                             key=self.keydown)
        canvas.set_draw_mode('draw')
        canvas.register_for_cursor_drawing(self.fitsimage)
        canvas.set_surface(self.fitsimage)
        self.canvas = canvas

        self.cuts_image = None

        self.gui_up = False

        vbox = Widgets.VBox()

        self.cuts_plot = plots.CutsPlot(logger=self.logger,
                                        width=700, height=400)
        self.points = []
        self.plot = Plot.PlotWidget(self.cuts_plot)
        self.plot.resize(400, 400)
        ax = self.cuts_plot.add_axis()
        ax.grid(True)
        vbox.add_widget(self.plot)
        control_hbox = Widgets.HBox()
        self.savedata = Widgets.Button("Save Data")
        self.savedata.add_callback('activated', self.save_data)
        self.savedata.set_enabled(False)
        control_hbox.add_widget(self.savedata)
        self.maxtogglebtn = Widgets.Button("Max Finder: Off")
        self.maxtogglebtn.add_callback('activated', self.max_finder_cb)
        self.maxtogglebtn.set_enabled(True)
        control_hbox.add_widget(self.maxtogglebtn)
        self.closebtn = Widgets.Button("Close")
        self.closebtn.add_callback('activated', self.dismiss)
        control_hbox.add_widget(self.closebtn)
        vbox.add_widget(control_hbox)
        self.add_widget(vbox)
        self.start()
        self.gui_up = True
        self.threadpool = QtCore.QThreadPool()

        self.max_toggle = False
        self.maxlinetag = "slit-line"

        self.fw = None

    def max_finder_cb(self, e):
        if self.max_toggle == False:
            self.max_toggle = True
            self.replot_all()
            self.maxtogglebtn.set_text("Max Finder: On")
        else:
            self.max_toggle = False
            self.replot_all()
            self.maxtogglebtn.set_text("Max Finder: Off")

    
    def delete_all(self):
        self.canvas.delete_all_objects()
        self.tags = [self._new_cut]
        self.cutstag = self._new_cut
        self.cuts_plot.clear()

    def add_cuts_tag(self, tag):
        if tag not in self.tags:
            self.tags.append(tag)

    def start(self):
        # start line cuts operation
        self.canvas.enable_draw(True)
        self.cuts_plot.set_titles(rtitle="Cuts")

        # insert canvas, if not already
        p_canvas = self.fitsimage.get_canvas()
        try:
            p_canvas.get_object_by_tag(self.layertag)

        except KeyError:
            # Add ruler layer
            p_canvas.add(self.canvas, tag=self.layertag)

        self.resume()

    def pause(self):
        self.canvas.ui_set_active(False)

    def resume(self):
        # turn off any mode user may be in
        # self.modes_off()

        self.canvas.ui_set_active(True, viewer=self.fitsimage)
        self.replot_all()

    def stop(self):
        self.gui_up = False
        # remove the canvas from the image
        p_canvas = self.fitsimage.get_canvas()
        p_canvas.delete_object_by_tag(self.layertag)
    
    def save_data(self, e):
        if self.fw != None:
            try:
                self.fw.dismiss(None)
            except AttributeError:
                pass
        self.fw = FileWriter(self.logger, self.points)
        self.fw.show()

    def get_max_pixels_on_line(self, x1, y1, x2, y2, image, getvalues=True):
        """Updated to look for max in 10 pixel range
        Uses Bresenham's line algorithm to enumerate the pixels along
        a line.
        (see http://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm)

        If `getvalues`==False then it will return tuples of (x, y) coordinates
        instead of pixel values.
        """
        # NOTE: seems to be necessary or we get a non-terminating result
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx > dy:
            direction = 'horizontal'
        else:
            direction = 'vertical'
        if x1 < x2:
            sx = 1
        else:
            sx = -1
        if y1 < y2:
            sy = 1
        else:
            sy = -1
        err = dx - dy

        res = []
        x, y = x1, y1
        x_path = []
        y_path = []
        while True:
            if getvalues:
                try:
                    max_arr = []
                    for i in range (-5, 5):
                        if direction == 'horizontal':
                            val = image.get_data_xy(x, y+i)
                        else:
                            val = image.get_data_xy(x+i, y)
                        max_arr.append(val)
                    val = np.max(max_arr)
                    max_index = max_arr.index(val)
                    if direction == 'horizontal':
                        y_update = y + max_index - 5
                        x_update = x
                    else:
                        x_update = x + max_index - 5
                        y_update = y
                except Exception:
                    val = np.nan
                res.append(val)
                x_path.append(x_update)
                y_path.append(y_update)
            else:
                res.append((x, y))
            if (x == x2) and (y == y2):
                break
            e2 = 2 * err
            if e2 > -dy:
                err = err - dy
                x += sx
            if e2 < dx:
                err = err + dx
                y += sy
        path = np.array((x_path, y_path)).T
        return res, path

    def redo(self):
        """This is called when a new image arrives or the data in the
        existing image changes.
        """

        self.replot_all()

    def _plotpoints(self, obj, color):

        try:
            self.fitsimage.get_canvas().get_object_by_tag(self.maxlinetag)
            self.fitsimage.get_canvas().delete_object_by_tag(self.maxlinetag)
        except KeyError:
            pass

        image = self.fitsimage.get_vip()  
        
        # Get points on the line
        if self.max_toggle == True:
            self.points, max_path = self.get_max_pixels_on_line(int(obj.x1), int(obj.y1), int(obj.x2), int(obj.y2), image)
            self.maxline = self.dc.Path(max_path, color='red')
            self.fitsimage.get_canvas().add(self.maxline, tag=self.maxlinetag, redraw=True)
        else:
            self.points = image.get_pixels_on_line(int(obj.x1), int(obj.y1), int(obj.x2), int(obj.y2))
        

        self.cuts_plot.cuts(self.points, title = f"Line Profile", xtitle="Line Index", ytitle="ADUs/COADD", color=color)
        self.savedata.set_enabled(True)


    def _replot(self, lines):
        for idx in range(len(lines)):
            line= lines[idx]
            self._plotpoints(line, "blue")

        return True

    def replot_all(self):
        self.cuts_plot.clear()
        # self.w.delete_all.set_enabled(False)
        # self.save_cuts.set_enabled(False)

        # idx = 0
        for cutstag in self.tags:
            if cutstag == self._new_cut:
                continue
            obj = self.canvas.get_object_by_tag(cutstag)
            lines = self._getlines(obj)
            self._replot(lines)

        self.cuts_plot.draw()

        self.canvas.redraw(whence=3)

        return True

    def _create_cut_obj(self, cuts_obj, color='cyan'):
        self.delete_all()
        text = "cut"
        # if not self.settings.get('label_cuts', False):
        #     text = ''
        cuts_obj.showcap = False
        cuts_obj.linestyle = 'solid'
        #cuts_obj.color = color
        color = cuts_obj.color
        args = [cuts_obj]
        text_obj = self.dc.Text(0, 0, text, color=color, coord='offset',
                                ref_obj=cuts_obj)
        args.append(text_obj)

        obj = self.dc.CompoundObject(*args)
        obj.set_data(cuts=True)

        return obj

    def _getlines(self, obj):
        return [obj.objects[0]]

    def buttondown_cb(self, canvas, event, data_x, data_y, viewer):
        return self.motion_cb(canvas, event, data_x, data_y, viewer)

    def motion_cb(self, canvas, event, data_x, data_y, viewer):


        if self.cutstag == self._new_cut:
            return True
        obj = self.canvas.get_object_by_tag(self.cutstag)
        # Assume first element of this compound object is the reference obj
        obj = obj.objects[0]
        obj.move_to_pt((data_x, data_y))
        canvas.redraw(whence=3)

        if self.drag_update:
            self.replot_all()
        return True

    def buttonup_cb(self, canvas, event, data_x, data_y, viewer):
        if self.cutstag == self._new_cut:
            return True
        obj = self.canvas.get_object_by_tag(self.cutstag)
        # Assume first element of this compound object is the reference obj
        obj = obj.objects[0]
        obj.move_to_pt((data_x, data_y))

        self.replot_all()
        return True

    def keydown(self, canvas, event, data_x, data_y, viewer):
        return True

    def draw_cb(self, canvas, tag):
        obj = canvas.get_object_by_tag(tag)
        canvas.delete_object_by_tag(tag)

        tag = "cut"

        cut = self._create_cut_obj(obj, color='cyan')
        cut.set_data(count=True)

        canvas.delete_object_by_tag(tag)
        self.canvas.add(cut, tag=tag)
        self.add_cuts_tag(tag)

        self.logger.debug("redoing cut plots")
        return self.replot_all()
    

    def dismiss(self, event):
        self.stop()
        self.delete()

class FitsViewer(QtGui.QMainWindow):

    def __init__(self, logger):
        super(FitsViewer, self).__init__()
        self.logger = logger

        # self.threadpool = QtCore.QThreadPool()

        fi = CanvasView(self.logger)
        fi.enable_autocuts('on')
        fi.set_autocut_params('zscale')
        fi.enable_autozoom('off')
        fi.enable_autocenter('off')
        # fi.set_color_map('YlOrBr_r')
        # fi.set_callback('drag-drop', self.drop_file)
        # fi.set_bg(0.2, 0.2, 0.2)
        fi.ui_set_active(True)
        self.fitsimage = fi

        # enable some user interaction
        menubar = self.menuBar()

        filemenu = menubar.addMenu("File")

        item = QtGui.QAction("Open File", menubar)
        item.triggered.connect(self.open_file)
        filemenu.addAction(item)

        sep = QtGui.QAction(menubar)
        sep.setSeparator(True)
        filemenu.addAction(sep)

        item = QtGui.QAction("Quit", menubar)
        item.triggered.connect(self.quit)
        filemenu.addAction(item)

        cutmenu = menubar.addMenu("Cuts")

        item = QtGui.QAction("Cut GUI", menubar)
        item.triggered.connect(self.cuts_popup)
        cutmenu.addAction(item)

        colormenu = menubar.addMenu("Colors")
        for cm_name in cmap.get_names():
            item = QtGui.QAction(cm_name, menubar)
            
            item.triggered.connect(partial(self.cmap_change, cm_name))
            colormenu.addAction(item)

        cutmenu = menubar.addMenu("Display Parameters")
        for cut_name in fi.get_autocut_methods():
            item = QtGui.QAction(cut_name, menubar)
            
            item.triggered.connect(partial(self.cut_change, cut_name))
            cutmenu.addAction(item)

        cutmenu = menubar.addMenu("Stretch")
        for stretch_name in fi.get_color_algorithms():
            item = QtGui.QAction(stretch_name, menubar)
            
            item.triggered.connect(partial(self.color_change, stretch_name))
            cutmenu.addAction(item)


        
        self.bd = fi.get_bindings()
        self.bd.enable_all(False)
        self.bm = fi.get_bindmap()
        self.bm.reset_mode(fi)
        vbox = QtGui.QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setObjectName("vbox")
        viewer_hbox = QtGui.QHBoxLayout()
        viewer_hbox.setObjectName("viewer_hbox")
        w = fi.get_widget()
        # w.setMinimumSize(QtCore.QSize(0, 650))
        viewer_hbox.addWidget(w)
        viewer_hbox.setContentsMargins(QtCore.QMargins(4,1,4,1))
        viewerHB = QtGui.QWidget()
        viewerHB.setLayout(viewer_hbox)
        buttons_vbox = QtGui.QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setObjectName("bvbox")
        click_hbox = QtGui.QHBoxLayout()
        click_hbox.setObjectName("click_hbox")
        self.clickinfo = QtGui.QLabel("Click the image to pan.")
        # self.clickinfo.setMinimumSize(QtCore.QSize(200, 0))
        self.clickinfo.setObjectName("clickinfo")
        click_hbox.addWidget(self.clickinfo)
        self.wzoomin = QtGui.QPushButton("Zoom In")
        self.wzoomin.setObjectName("wzoomin")
        self.wzoomin.clicked.connect(self.zoomIn)
        self.wzoomin.setMaximumSize(QtCore.QSize(75, 40))
        click_hbox.addWidget(self.wzoomin)
        self.wzoomreset = QtGui.QPushButton("Reset Zoom")
        self.wzoomreset.setObjectName("wzoomreset")
        self.wzoomreset.clicked.connect(self.zoomReset)
        self.wzoomreset.setMaximumSize(QtCore.QSize(80, 40))
        click_hbox.addWidget(self.wzoomreset)
        self.wzoomout = QtGui.QPushButton("Zoom Out")
        self.wzoomout.setObjectName("wzoomout")
        self.wzoomout.clicked.connect(self.zoomOut)
        self.wzoomout.setMaximumSize(QtCore.QSize(75, 40))
        click_hbox.addWidget(self.wzoomout)
        self.wrecenter = QtGui.QPushButton("Re-center Image")
        self.wrecenter.setObjectName("wrecenter")
        self.wrecenter.clicked.connect(self.recenter)
        click_hbox.addWidget(self.wrecenter)
        click_hbox.setContentsMargins(QtCore.QMargins(4,1,4,1))
        hw = QtGui.QWidget()
        hw.setLayout(click_hbox)

        buttons_vbox.addWidget(hw)
        readout_hbox = QtGui.QHBoxLayout()
        readout_hbox.setObjectName("readout_hbox")
        self.readout = QtGui.QLabel("X: Y:  RA:  DEC: Value:")
        self.readout.setObjectName("readout")
        # self.readout.setMinimumSize(QtCore.QSize(350, 0))
        readout_hbox.addWidget(self.readout)
        readout_hbox.setContentsMargins(QtCore.QMargins(4,1,4,1))
        hw = QtGui.QWidget()
        hw.setLayout(readout_hbox)
        buttons_vbox.addWidget(hw)
        file_hbox = QtGui.QHBoxLayout()
        file_hbox.setObjectName("file_hbox")
        self.file_info = QtGui.QLabel("File: ")
        self.file_info.setObjectName("file_info")
        # self.file_info.setMinimumSize(QtCore.QSize(350, 0))
        file_hbox.addWidget(self.file_info)

        file_hbox.setContentsMargins(QtCore.QMargins(4,1,4,1))
        hw = QtGui.QWidget()
        hw.setLayout(file_hbox)
        buttons_vbox.addWidget(hw)

        buttonsVB = QtGui.QWidget()
        buttonsVB.setLayout(buttons_vbox)

        splitter = QtGui.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(viewerHB)
        splitter.addWidget(buttonsVB)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([600, 40])
        vbox.addWidget(splitter)

        vw = QtGui.QWidget()
        self.setCentralWidget(vw)
        vw.setLayout(vbox)

        fi.set_callback('cursor-changed', self.motion_cb)
        fi.add_callback('cursor-down', self.btndown)

        self.c = None

        self.fitsimage.set_color_map('heat')

        self.base_zoom = 0
        

    def add_canvas(self, tag=None):
        # add a canvas to the view
        my_canvas = self.fitsimage.get_canvas()
        RecCanvas = my_canvas.get_draw_class('rectangle')
        # CompCanvas = my_canvas.get_draw_class('compass')
        # return RecCanvas, CompCanvas
        return RecCanvas

    def cmap_change(self, cm_name):
        self.fitsimage.set_color_map(cm_name)

    def cut_change(self, cut_name):
        self.fitsimage.set_autocut_params(cut_name)

    def color_change(self, stretch_name):
        self.fitsimage.set_color_algorithm(stretch_name)

    def motion_cb(self, viewer, button, data_x, data_y):

        fits_x, fits_y = data_x, data_y

        # Get the value under the data coordinates
         # Calculate WCS RA
        try:
            # NOTE: image function operates on DATA space coords
            image = viewer.get_image()
            if image is None:
                # No image loaded
                return
            ra_txt, dec_txt = image.pixtoradec(fits_x, fits_y,
                                               format='str', coords='fits')
        except Exception as e:
            # self.logger.warning("Bad coordinate conversion: %s" % (
            #     str(e)))
            ra_txt = 'BAD WCS'
            dec_txt = 'BAD WCS'

        try:
            # We report the value across the pixel, even though the coords
            # change halfway across the pixel
            value = viewer.get_data(int(data_x + 0.5), int(data_y + 0.5))

        except Exception:
            value = None

        fits_x, fits_y = data_x, data_y

        if (fits_x > 2048 or fits_x <0) or (fits_y > 2048 or fits_y <0): #TODO get actual values
            text = "X: Y:  RA:  Dec:  Value:"
            self.readout.setText(text)
        else:
            text = f"X: {int(fits_x)} Y: {int(fits_y)}  RA: {ra_txt}  Dec: {dec_txt}  Value: {value}"
            self.readout.setText(text)

    def quit(self, *args):
        self.logger.info("Attempting to shut down the application...")
        time.sleep(1)
        # self.threadpool = False
        QtGui.QApplication.instance().quit()

    def load_file(self, filepath):
            filepath = os.path.join(filepath, filepath)
            fitsData = fits.getdata(filepath)
            header = fits.getheader(filepath)
            if self.fitsimage.get_image() == None:
                recenter = True
            image = load_data(self.writeFits(header, fitsData), logger=self.logger)
            self.fitsimage.set_image(image)
                # self.setWindowTitle(filepath)
            if recenter == True:
                self.recenter()
            print(f"Loaded {filepath}")
            self.file_info.setText(f"File: {filepath}")
            self.base_zoom = self.fitsimage.get_zoom()

    def open_file(self):
            filters = "Images (*.fz)"
            selected_filter = "Images (*.fz)"
            res = QtGui.QFileDialog.getOpenFileName(self, "Open FITS file",
                                                    ".", filters, selected_filter)
            if isinstance(res, tuple):
                fileName = res[0]
            else:
                fileName = str(res)
            if len(fileName) != 0:
                self.load_file(fileName)

    def cuts_popup(self):
        if self.c != None:
            try:
                self.c.dismiss(None)
            except AttributeError:
                pass
        self.c = Cuts(self.logger, self.fitsimage, self.bm)
        self.c.show()

    def writeFits(self, headerinfo, image_data):
        hdu = fits.PrimaryHDU(header=headerinfo, data=image_data)
        filename = 'subImage.fits'
        try:
            hdu.writeto(filename)
        except OSError:
            os.remove(filename)
            hdu.writeto(filename)
        return filename
    
    def zoomIn(self):
        current = self.fitsimage.get_zoom()
        return self.fitsimage.zoom_to(current + 1)
    
    def zoomReset(self):
        return self.fitsimage.zoom_to(self.base_zoom)
    
    def zoomOut(self):
        current = self.fitsimage.get_zoom()
        return self.fitsimage.zoom_to(current - 1)
    
    def recenter(self):
        self.fitsimage.zoom_fit()
        self.base_zoom = self.fitsimage.get_zoom()

    def btndown(self, canvas, event, data_x, data_y):
        self.xclick = data_x
        self.yclick = data_y
        self.fitsimage.set_pan(data_x, data_y)
        # self.pickstar(self.fitsimage)

def main():
    app = QtGui.QApplication([])

    # ginga needs a logger.
    # If you don't want to log anything you can create a null logger by
    # using null=True in this call instead of log_stderr=True
    logger = log.get_logger("DriftExtracter", log_stderr=True, level=40, log_file="DE.log")
    w = FitsViewer(logger)
    w.resize(1000,950)
    w.show()
    app.setActiveWindow(w)
    w.raise_()
    w.activateWindow()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()