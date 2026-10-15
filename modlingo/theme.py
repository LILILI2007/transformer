"""Original farm-inspired Canvas artwork and shared, dependency-free ttk styles."""
import tkinter as tk
from tkinter import ttk


COLORS = {
    'page': '#eee3cb', 'paper': '#fff7e3', 'ink': '#443125', 'muted': '#78634b',
    'wood': '#805231', 'edge': '#b78249', 'gold': '#f0c675', 'green': '#3e6948',
    'sky': '#b4dbe1', 'line': '#dcc8a3', 'white': '#fffdf6',
}
FONT = 'Microsoft YaHei UI'


def scale_for(root):
    return max(1.0, root.winfo_fpixels('1i') / 96)


def fit_window(root, width=1240, height=850, minimum=(900, 650)):
    scale = scale_for(root)
    width = min(round(width * scale), root.winfo_screenwidth() - 60)
    height = min(round(height * scale), root.winfo_screenheight() - 90)
    root.geometry('%dx%d' % (width, height))
    root.minsize(min(round(minimum[0] * scale), width), min(round(minimum[1] * scale), height))


def apply_theme(root):
    c = COLORS
    root.configure(background=c['page'])
    style = ttk.Style(root)
    if style.theme_use() != 'clam':
        style.theme_use('clam')
    style.configure('.', font=(FONT, 10), foreground=c['ink'], background=c['page'])
    style.configure('TFrame', background=c['page'])
    style.configure('Paper.TFrame', background=c['paper'])
    style.configure('TLabel', background=c['page'], foreground=c['ink'])
    style.configure('Paper.TLabel', background=c['paper'], foreground=c['ink'])
    style.configure('Muted.TLabel', foreground=c['muted'])
    style.configure('PaperMuted.TLabel', background=c['paper'], foreground=c['muted'], font=(FONT, 9))
    style.configure('Section.TLabel', background=c['paper'], foreground=c['wood'], font=(FONT, 11, 'bold'))
    style.configure('Metric.TLabel', background=c['paper'], foreground=c['wood'], font=('Segoe UI', 24, 'bold'))
    style.configure('GoodMetric.TLabel', background=c['paper'], foreground=c['green'], font=('Segoe UI', 24, 'bold'))
    style.configure('AlertMetric.TLabel', background=c['paper'], foreground='#a04832', font=('Segoe UI', 24, 'bold'))
    style.configure('TButton', padding=(12, 8), background='#f3dfb5', foreground=c['ink'],
                    bordercolor=c['edge'], lightcolor='#fff2cd', darkcolor=c['edge'], borderwidth=2, relief='raised')
    style.map('TButton', background=[('disabled', '#e1d6bf'), ('pressed', '#deb477'), ('active', '#ffe9b9')],
              foreground=[('disabled', '#8a7a61')], bordercolor=[('focus', c['green'])])
    style.configure('Accent.TButton', background=c['green'], foreground=c['white'], bordercolor='#284e32',
                    lightcolor='#749465', darkcolor='#284e32', font=(FONT, 10, 'bold'))
    style.map('Accent.TButton', background=[('disabled', '#bbc5ad'), ('pressed', '#294b34'), ('active', '#527f52')],
              foreground=[('disabled', '#52624c'), ('!disabled', c['white'])])
    style.configure('Quiet.TButton', background=c['paper'], borderwidth=1, padding=(9, 6))
    style.configure('TEntry', fieldbackground=c['white'], foreground=c['ink'], bordercolor=c['edge'], padding=7)
    style.configure('TCombobox', fieldbackground=c['white'], background='#ead2a5', foreground=c['ink'],
                    bordercolor=c['edge'], arrowcolor=c['wood'], padding=6)
    style.map('TCombobox', fieldbackground=[('readonly', c['white']), ('disabled', '#e2d8c1')],
              foreground=[('readonly', c['ink'])], selectbackground=[('readonly', c['green'])])
    root.option_add('*TCombobox*Listbox.background', c['white'])
    root.option_add('*TCombobox*Listbox.foreground', c['ink'])
    root.option_add('*TCombobox*Listbox.selectBackground', c['green'])
    style.configure('Treeview', rowheight=round(34 * scale_for(root)), font=(FONT, 10),
                    background=c['white'], fieldbackground=c['white'], foreground=c['ink'],
                    borderwidth=0, bordercolor=c['line'])
    style.configure('Treeview.Heading', background='#efd7a8', foreground=c['wood'],
                    font=(FONT, 10, 'bold'), padding=(8, 10), bordercolor=c['edge'], relief='flat')
    style.map('Treeview', background=[('selected', '#517653')], foreground=[('selected', '#fffdf2')])
    style.map('Treeview.Heading', background=[('active', '#e8c68d')])
    style.configure('Horizontal.TProgressbar', background='#6f8d48', troughcolor='#e4d4b5',
                    bordercolor=c['edge'], lightcolor='#91ad63', darkcolor='#52713d', thickness=12)
    for axis in ('Vertical', 'Horizontal'):
        style.configure(axis + '.TScrollbar', background='#d5b27f', troughcolor=c['paper'],
                        bordercolor=c['line'], arrowcolor=c['wood'])
    return style


def paper_panel(parent, padding=16):
    border = tk.Frame(parent, bg=COLORS['wood'], bd=0)
    inner = tk.Frame(border, bg=COLORS['edge'], bd=0)
    inner.pack(fill='both', expand=True, padx=2, pady=2)
    frame = ttk.Frame(inner, style='Paper.TFrame', padding=padding)
    frame.pack(fill='both', expand=True, padx=2, pady=2)
    return border, frame


def style_rows(tree):
    tree.tag_configure('even', background='#fffdf6', foreground=COLORS['ink'])
    tree.tag_configure('odd', background='#faf1dd', foreground=COLORS['ink'])
    tree.tag_configure('good', background='#e9f0dc', foreground='#315c3d')
    tree.tag_configure('attention', background='#fff0d5', foreground='#87551f')
    tree.tag_configure('failed', background='#f9e6dd', foreground='#8d3d2c')


class FarmBanner(tk.Canvas):
    """Scalable original pixel landscape, with no downloaded game assets."""
    def __init__(self, parent, title='ModLingo', subtitle='让喜欢的 mod，在农场里说中文。', compact=False):
        self.scale = scale_for(parent)
        self.compact = compact
        self.title = title
        self.subtitle = subtitle
        super().__init__(parent, height=round((95 if compact else 148) * self.scale),
                         background=COLORS['sky'], highlightthickness=0, bd=0)
        self.bind('<Configure>', self.draw)

    def draw(self, event=None):
        self.delete('all')
        s = self.scale
        width = max(self.winfo_width() / s, 500)
        height = 95 if self.compact else 148
        def rect(x, y, w, h, color):
            self.create_rectangle(round(x*s), round(y*s), round((x+w)*s), round((y+h)*s), fill=color, outline='')
        def poly(points, color):
            self.create_polygon(*[round(v*s) for point in points for v in point], fill=color, outline='')
        rect(0, 0, width, height, '#b6dfe4')
        rect(0, height*.43, width, height*.57, '#cde7df')
        for x, y in [(width*.46, 18), (width*.70, 12), (width*.92, 27)]:
            rect(x, y+5, 54, 12, '#f5fbef'); rect(x+10, y, 29, 22, '#f5fbef')
        for x, peak, w in [(width*.34, height*.55, 200), (width*.58, height*.30, 220), (width*.81, height*.48, 230)]:
            poly([(x, height), (x+w*.18, peak+22), (x+w*.28, peak+22), (x+w*.42, peak),
                  (x+w*.55, peak), (x+w*.7, peak+26), (x+w*.8, peak+26), (x+w, height)], '#7daa9c')
        rect(0, height-23, width, 23, '#799653')
        rect(0, height-19, width, 19, '#91ab63')
        # Farm scene stays on the right, separate from the readable title board.
        if width > 740:
            x, y = width-187, height-79
            rect(x, y+24, 75, 43, '#c89961')
            rect(x+5, y+29, 65, 4, '#e7b67a')
            poly([(x-8,y+24),(x+16,y),(x+59,y),(x+83,y+24)], '#805241')
            rect(x+8,y+13,60,5,'#a3664a'); rect(x+49,y-4,10,16,'#7d5140')
            rect(x+15,y+35,15,15,'#f7dfa0'); rect(x+19,y+35,3,15,'#875a3e')
            rect(x+46,y+39,16,28,'#815a3c'); rect(x+57,y+51,3,3,'#f3d492')
            rect(x-16,y+67,108,4,'#6c7947')
            for dx in (-56,-30,0,26):
                rect(x+dx,y+72,18,5,'#856242')
                rect(x+dx+8,y+60,3,12,'#456d3f')
                rect(x+dx+2,y+58,7,5,'#4c7b42'); rect(x+dx+10,y+55,8,5,'#638b42')
            # Tiny chicken with a red comb and gold beak.
            rect(x+105,y+52,16,13,'#fff8df'); rect(x+115,y+46,10,11,'#fff8df')
            rect(x+117,y+43,5,4,'#bd6745'); rect(x+125,y+51,5,3,'#d79a43')
            rect(x+121,y+49,2,2,'#60442e'); rect(x+109,y+65,2,5,'#b4863e')
            rect(x+117,y+65,2,5,'#b4863e')
            for tx in (width-310,width-52):
                rect(tx+14,height-63,7,43,'#856042')
                rect(tx+3,height-75,29,29,'#486f4b'); rect(tx-4,height-63,43,22,'#587d4d')
                rect(tx+7,height-79,20,10,'#6d9157')
        # Signboard, corner pegs and a sprout emblem are all original geometry.
        board_width = min(475, width-32)
        rect(16, 14, board_width, height-(22 if self.compact else 37), '#795236')
        rect(20, 18, board_width-8, height-(30 if self.compact else 45), '#c39358')
        rect(24, 22, board_width-16, height-(38 if self.compact else 53), '#f5dfaf')
        for px in (27, board_width+1):
            for py in (25, height-(19 if self.compact else 34)):
                rect(px,py,4,4,'#9b703e')
        ox, oy = 42, 36 if self.compact else 52
        rect(ox+12,oy+8,4,25,'#477049'); rect(ox,oy,15,10,'#6c944e')
        rect(ox+17,oy-5,16,10,'#84a557'); rect(ox+17,oy+5,8,5,'#507843')
        self.create_text(round(91*s), round((37 if self.compact else 51)*s), anchor='w',
                         text=self.title, fill='#5c3b27', font=(FONT if any(ord(ch) > 127 for ch in self.title) else 'Segoe UI', 24 if self.compact else 29, 'bold'))
        self.create_text(round(91*s), round((65 if self.compact else 84)*s), anchor='w',
                         text=self.subtitle, fill='#775735', font=(FONT, 9 if self.compact else 10))
        rect(0, height-4, width, 4, '#745537')
