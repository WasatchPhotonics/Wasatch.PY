class AreaScanImage:

    FRAME_COUNT = 0

    def __init__(self, data=None, width=None, height=None, format_name=None, width_orig=None, height_orig=None, pathname_png=None):
        self.data = data
        self.width = width
        self.height = height
        self.width_orig = width_orig
        self.height_orig = height_orig
        self.format_name = format_name
        self.pathname_png = pathname_png

        self.frame_count = AreaScanImage.FRAME_COUNT
        AreaScanImage.FRAME_COUNT += 1

    def __repr__(self):
        return f"AreaScanImage<frame {self.frame_count}, width {self.width} (orig {self.width_orig}), height {self.height} (orig {self.height_orig}), format_name {self.format_name}, pathname_png {self.pathname_png}>"
