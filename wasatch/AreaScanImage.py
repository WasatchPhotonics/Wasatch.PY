class AreaScanImage:
    def __init__(self, data=None, width=None, height=None, format_name=None, width_orig=None, height_orig=None, pathname_png=None):
        self.data = data
        self.width = width
        self.height = height
        self.width_orig = width_orig
        self.height_orig = height_orig
        self.format_name = format_name
        self.pathname_png = pathname_png

    def __repr__(self):
        return f"AreaScanImage<width {self.width}, height {self.height}, format_name {self.format_name}, pathname_png {self.pathname_png}>"
