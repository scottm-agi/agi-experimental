from __future__ import annotations
try:  # webcolors is optional; provide a minimal fallback for tests/dev
    import webcolors  # type: ignore[import]
except ImportError:  # pragma: no cover - simple, non-critical fallback
    class _WebColorsFallback:
        """Fallback providing a compatible ``name_to_rgb`` API.

        Maps common CSS color names to their actual RGB values so that
        error/warning/success colors render correctly even when the
        ``webcolors`` package is unavailable. This fixes ITR-52 D-11
        where errors were invisible (white-on-white) because all names
        mapped to (255, 255, 255).
        """

        # Common color names used in AGIX's PrintStyle
        _COLOR_MAP = {
            "red": (255, 80, 80),
            "green": (0, 128, 0),
            "blue": (0, 0, 255),
            "yellow": (255, 255, 0),
            "orange": (255, 165, 0),
            "cyan": (0, 255, 255),
            "magenta": (255, 0, 255),
            "white": (255, 255, 255),
            "black": (0, 0, 0),
            "gray": (128, 128, 128),
            "grey": (128, 128, 128),
            "purple": (128, 0, 128),
            "pink": (255, 192, 203),
            "brown": (165, 42, 42),
            "lime": (0, 255, 0),
            "navy": (0, 0, 128),
            "teal": (0, 128, 128),
            "maroon": (128, 0, 0),
            "olive": (128, 128, 0),
            "aqua": (0, 255, 255),
            "silver": (192, 192, 192),
        }

        @staticmethod
        def name_to_rgb(name: str):  # type: ignore[override]
            class RGB:
                def __init__(self, r: int, g: int, b: int) -> None:
                    self.red = r
                    self.green = g
                    self.blue = b

            lookup = _WebColorsFallback._COLOR_MAP.get(name.lower())
            if lookup is None:
                raise ValueError(f"'{name}' is not a recognized color name")
            r, g, b = lookup
            return RGB(r, g, b)

    webcolors = _WebColorsFallback()  # type: ignore

import os, html, re
import sys
from datetime import datetime
from python.helpers import files

class PrintStyle:
    last_endline = True
    log_file_path = None

    def __init__(self, bold=False, italic=False, underline=False, font_color="default", background_color="default", padding=False, log_only=False, file=None):
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.font_color = font_color
        self.background_color = background_color
        self.padding = padding
        self.padding_added = False  # Flag to track if padding was added
        self.log_only = log_only
        
        # Default to stderr if running as an MCP server to avoid stdout pollution
        if file is None:
            if os.environ.get("AGIX_MCP_SERVER") == "true":
                self.file = sys.stderr
            else:
                self.file = sys.stdout
        else:
            self.file = file

        if PrintStyle.log_file_path is None:
            logs_dir = files.get_abs_path("logs")
            os.makedirs(logs_dir, exist_ok=True)
            log_filename = datetime.now().strftime("log_%Y%m%d_%H%M%S.html")
            PrintStyle.log_file_path = os.path.join(logs_dir, log_filename)
            with open(PrintStyle.log_file_path, "w") as f:
                f.write("<html><body style='background-color:black;font-family: Arial, Helvetica, sans-serif;'><pre>\n")

    def _get_rgb_color_code(self, color, is_background=False):
        try:
            if color.startswith("#") and len(color) == 7:
                r = int(color[1:3], 16)
                g = int(color[3:5], 16)
                b = int(color[5:7], 16)
            else:
                rgb_color = webcolors.name_to_rgb(color)
                r, g, b = rgb_color.red, rgb_color.green, rgb_color.blue

            if is_background:
                return f"\033[48;2;{r};{g};{b}m", f"background-color: rgb({r}, {g}, {b});"
            else:
                return f"\033[38;2;{r};{g};{b}m", f"color: rgb({r}, {g}, {b});"
        except ValueError:
            return "", ""

    def _get_styled_text(self, text):
        start = ""
        end = "\033[0m"  # Reset ANSI code
        if self.bold:
            start += "\033[1m"
        if self.italic:
            start += "\033[3m"
        if self.underline:
            start += "\033[4m"
        font_color_code, _ = self._get_rgb_color_code(self.font_color)
        background_color_code, _ = self._get_rgb_color_code(self.background_color, True)
        start += font_color_code
        start += background_color_code
        return start + text + end

    def _get_html_styled_text(self, text):
        styles = []
        if self.bold:
            styles.append("font-weight: bold;")
        if self.italic:
            styles.append("font-style: italic;")
        if self.underline:
            styles.append("text-decoration: underline;")
        _, font_color_code = self._get_rgb_color_code(self.font_color)
        _, background_color_code = self._get_rgb_color_code(self.background_color, True)
        styles.append(font_color_code)
        styles.append(background_color_code)
        style_attr = " ".join(styles)
        escaped_text = html.escape(text).replace("\n", "<br>")  # Escape HTML special characters
        return f'<span style="{style_attr}">{escaped_text}</span>'

    @staticmethod
    def _html_timestamp():
        """Return an HTML timestamp span: [HH:MM:SS.mmm] in gray.
        
        R-2 fix (ITR-52 D-3): Every HTML log line needs a timestamp for
        timeline reconstruction and cross-source correlation.
        Performance: datetime.now().strftime() is ~0.001ms per call.
        """
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm
        return f'<span style="color: rgb(128,128,128);">[{ts}]</span> '

    def _add_padding_if_needed(self):
        if self.padding and not self.padding_added:
            if not self.log_only:
                try:
                    print(file=self.file)
                except BlockingIOError:
                    pass  # Skip padding on buffer overflow, non-critical
            self._log_html("<br>")
            self.padding_added = True

    def _log_html(self, html):
        try:
            with open(PrintStyle.log_file_path, "a", encoding='utf-8', errors='replace') as f:
                f.write(html)
        except Exception as e:
            # Last resort: fallback to simpler write or ignore if completely stuck
            try:
                with open(PrintStyle.log_file_path, "a", encoding='ascii', errors='ignore') as f:
                    f.write(str(html))
            except OSError:
                pass

    @staticmethod
    def _close_html_log():
        if PrintStyle.log_file_path:
            with open(PrintStyle.log_file_path, "a") as f:
                f.write("</pre></body></html>")

    @staticmethod
    def _sanitize_surrogates(text: str) -> str:
        """Remove Unicode surrogate characters that crash print().

        Surrogates (U+D800–U+DFFF) are illegal in UTF-8 and cause
        UnicodeEncodeError when Python tries to write them to stdout.
        Replace them with the Unicode replacement character U+FFFD.
        """
        # re.sub with the surrogate range is the fastest pure-Python approach
        return re.sub(r'[\ud800-\udfff]', '\ufffd', text)

    def get(self, *args, sep=' ', **kwargs):
        text = sep.join(map(str, args))

        # Strip surrogate characters that would crash print()
        text = self._sanitize_surrogates(text)
        
        # Automatically mask secrets in all print output
        try:
            if not hasattr(self, "secrets_mgr"):
                from python.helpers.secrets_helper import get_secrets_manager
                self.secrets_mgr = get_secrets_manager()
            text = self.secrets_mgr.mask_values(text)
        except Exception:
            # If masking fails, proceed without masking to avoid breaking functionality
            pass
        
        return text, self._get_styled_text(text), self._get_html_styled_text(text)

    def print(self, *args, sep=' ', file=None, **kwargs):
        self._add_padding_if_needed()
        if file is None: file = self.file
        if not PrintStyle.last_endline:
            try:
                print(file=file)
            except BlockingIOError:
                pass  # Skip newline on buffer overflow
            self._log_html("<br>")
        plain_text, styled_text, html_text = self.get(*args, sep=sep, **kwargs)
        if not self.log_only:
            try:
                print(styled_text, end='\n', flush=True, file=file)
            except BlockingIOError:
                # Buffer full, retry with brief pause
                import time
                try:
                    time.sleep(0.01)
                    print(styled_text, end='\n', flush=True, file=file)
                except BlockingIOError:
                    try:
                        print(styled_text, end='\n', flush=False, file=file)
                    except BlockingIOError:
                        pass  # Skip console, HTML log still captures it
            except UnicodeEncodeError:
                # Surrogates or other unencodable chars — encode with replace
                safe = styled_text.encode('utf-8', errors='replace').decode('utf-8')
                try:
                    print(safe, end='\n', flush=True, file=file)
                except Exception:
                    pass
        # R-2 (ITR-52 D-3): Prepend timestamp to every HTML log line
        self._log_html(self._html_timestamp() + html_text + "<br>\n")
        PrintStyle.last_endline = True

    def stream(self, *args, sep=' ', file=None, **kwargs):
        self._add_padding_if_needed()
        if file is None: file = self.file
        plain_text, styled_text, html_text = self.get(*args, sep=sep, **kwargs)
        if not self.log_only:
            try:
                print(styled_text, end='', flush=True, file=file)
            except BlockingIOError:
                # Buffer full from rapid output (e.g., npm build spinners)
                # Retry with brief pause for buffer to drain
                import time
                try:
                    time.sleep(0.01)
                    print(styled_text, end='', flush=True, file=file)
                except BlockingIOError:
                    # Last resort: write without flush, HTML log continues unaffected
                    try:
                        print(styled_text, end='', flush=False, file=file)
                    except BlockingIOError:
                        # Skip console output entirely, HTML log still captures it
                        pass
            except UnicodeEncodeError:
                # Surrogates or other unencodable chars — encode with replace
                safe = styled_text.encode('utf-8', errors='replace').decode('utf-8')
                try:
                    print(safe, end='', flush=True, file=file)
                except Exception:
                    pass
        self._log_html(html_text)
        PrintStyle.last_endline = False

    def is_last_line_empty(self):
        lines = sys.stdin.readlines()
        return bool(lines) and not lines[-1].strip()

    @staticmethod
    def _is_production() -> bool:
        """Returns True when running in production (Railway)."""
        return bool(os.environ.get("RAILWAY_ENVIRONMENT"))

    @staticmethod
    def standard(text: str):
        log_only = PrintStyle._is_production()
        PrintStyle(log_only=log_only).print(text)

    @staticmethod
    def hint(text: str):
        log_only = PrintStyle._is_production()
        PrintStyle(font_color="#6C3483", padding=True, log_only=log_only).print("Hint: "+text)

    @staticmethod
    def info(text: str):
        log_only = PrintStyle._is_production()
        PrintStyle(font_color="#0000FF", padding=True, log_only=log_only).print("Info: "+text)

    @staticmethod
    def success(text: str):
        log_only = PrintStyle._is_production()
        PrintStyle(font_color="#008000", padding=True, log_only=log_only).print("Success: "+text)

    @staticmethod
    def warning(text: str):
        log_only = PrintStyle._is_production()
        PrintStyle(font_color="#FFA500", padding=True, log_only=log_only).print("Warning: "+text)

    @staticmethod
    def debug(text: str):
        # Debug: log-only by default. Prints to console only when DEBUG_MODE=1
        # and NOT in production. Production always suppresses debug to console.
        if PrintStyle._is_production():
            log_only = True
        else:
            log_only = os.environ.get("DEBUG_MODE", "").strip() != "1"
        PrintStyle(font_color="#808080", padding=True, log_only=log_only).print("Debug: "+text)

    @staticmethod
    def error(text: str):
        # Errors ALWAYS print to console, even in production
        PrintStyle(font_color="red", padding=True).print("Error: "+text)

# Ensure HTML file is closed properly when the program exits
import atexit
atexit.register(PrintStyle._close_html_log)
