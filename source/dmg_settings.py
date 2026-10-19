"""Finder layout for dmgbuild; paths are supplied by package_dmg.sh."""

format = "UDZO"
filesystem = "HFS+"
files = [defines["app"], (defines["guide"], "Установка.txt")]
symlinks = {"Applications": "/Applications"}
background = defines["background"]
window_rect = ((160, 120), (640, 420))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 96
text_size = 14
icon_locations = {
    "Telegram Focus.app": (160, 210),
    "Applications": (480, 210),
    "Установка.txt": (320, 345),
}
hide_extensions = ["Telegram Focus.app"]
