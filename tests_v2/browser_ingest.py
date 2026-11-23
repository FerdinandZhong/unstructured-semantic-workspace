"""Exercise local originals in Chromium against the V2 loopback server."""

import io
import struct
import sys
import wave
import zlib
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (1440, 980, 390)
FINANCE = "financial-services"
FACTORY = "manufacturing"
AMENDMENT = ROOT / "demo_data/manufacturing/documents/markdown/MFG-003.md"
JADE = ROOT / "demo_data/preload/adverse1536_NEWS-CORP0006700_jade-logistics-pte-ltd.md"


def upload_bytes(name, mime, data):
    return {"name": name, "mimeType": mime, "buffer": data}


def png_fixture():
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\x00\x2d\x62\xdf")) + chunk(b"IEND", b""))


def audio_fixture():
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setparams((1, 2, 8000, 8000, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 8000)
    return upload_bytes("recording.wav", "audio/wav", data.getvalue())


def video_fixture(page):
    data = page.evaluate("""async () => {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 64;
      const image = canvas.getContext('2d');
      const stream = canvas.captureStream(15);
      const recorder = new MediaRecorder(stream, {mimeType:'video/webm'});
      const chunks = [];
      recorder.ondataavailable = event => chunks.push(event.data);
      const stopped = new Promise(resolve => { recorder.onstop = resolve; });
      recorder.start();
      for (let index = 0; index < 6; index++) {
        image.fillStyle = index % 2 ? '#2d62df' : '#182642';
        image.fillRect(0, 0, 64, 64);
        await new Promise(resolve => setTimeout(resolve, 75));
      }
      recorder.stop();
      await stopped;
      stream.getTracks().forEach(track => track.stop());
      return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
    }""")
    return upload_bytes("recording.webm", "video/webm", bytes(data))


def exercise(base):
    errors = []
    with sync_playwright() as tools:
        browser = tools.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/ocr", lambda route: route.fulfill(
            status=200 if route.request.method == "GET" else 503,
            json={"available": False, "model": None}
        ))
        original_requests = []
        page.on("request", lambda request: original_requests.append(request.url)
                if "/api/original" in request.url else None)

        def visit(dataset, source=""):
            fragment = "/" + source if source else ""
            page.goto(base + f"#{dataset}/ingest{fragment}")
            page.wait_for_url(base + f"#{dataset}/ingest{fragment}")
            page.wait_for_function(
                "([id, detail]) => locationView?.dataset === id && (locationView.detail || '') === detail",
                arg=[dataset, source.split("/")[0]],
            )
            expect(page.locator("#ingest-title")).to_have_text("Sources")   # the Ingest step is now named Sources

        def upload(file):
            page.locator("#workspace-file").set_input_files(file)
            expect(page.locator(".file-library")).to_be_visible()
            row = page.locator(".file-added .file-open").first
            expect(row).to_be_focused()
            expect(page.locator(".ingest-split")).to_have_count(0)
            row.click()

        def open_file(source):
            page.locator(f'[data-file-id="{source}"]').click()

        def tab(name):
            page.locator(f'[data-ingest-tab="{name}"]').click()

        def action(name):
            return page.locator(f'[data-action="ingest-{name}"]')

        original = page.locator("#original-content")
        files = page.locator("#ingest-source")
        first_text = "First original.\nThe complete second line."
        second_text = "Second original with the same name."
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            before = len(original_requests)
            visit(FINANCE)
            expect(page.locator(".file-library")).to_be_visible()
            expect(page.locator(".ingest-split")).to_have_count(0)
            assert len(original_requests) == before
            expect(page.locator('[data-file-id="looker"]')).to_have_count(0)
            search = page.locator("#file-search")
            search.fill("no such file")
            expect(page.locator("#file-list-empty")).to_contain_text("No files match")
            search.fill("")
            open_file("cleaned_pdf_3")
            expect(files).to_have_value("cleaned_pdf_3")
            expect(original.locator("object")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.go_back()
            expect(page.locator(".file-library")).to_be_visible()
            expect(page.locator('[data-file-id="cleaned_pdf_3"]')).to_be_focused()
            page.go_forward()
            expect(original.locator("object")).to_be_visible()
            upload(upload_bytes("image.png", "image/png", png_fixture()))
            expect(original.locator("img")).to_be_visible()
            page.wait_for_function("document.querySelector('.original-image')?.naturalWidth === 1")
            expect(action("run")).to_be_disabled()
            action("prepare").click()
            expect(page.locator(".ingest-output")).to_contain_text("image.png")
            expect(page.locator(".ingest-results")).to_contain_text("Not sent")
            expect(original.locator("img")).to_be_visible()
            action("remove").click()
            expect(page.locator(".file-library")).to_be_visible()
            expect(page.locator(".file-open").filter(has_text="image.png")).to_have_count(0)
            visit(FACTORY)
            expect(page.locator('[data-file-id="orders"]')).to_have_count(0)
            open_file("amendment")
            expect(original.locator("pre")).to_have_text(AMENDMENT.read_text().strip())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

        visit(FINANCE, "cleaned_pdf_3")
        pages = page.locator('[data-setting="pages"]')
        open_settings(page)
        for value in ("0", "4-1", "1,", "1.5", "word"):
            pages.fill(value)
            expect(page.locator("#pages-error")).to_be_visible()
            expect(action("prepare")).to_be_disabled()
        pages.fill("1-3,8")
        action("prepare").click()
        expect(page.locator(".ingest-output")).to_contain_text("1-3,8")
        visit(FINANCE, "cleaned_pdf_1")
        expect(page.locator(".ingest-xfa")).to_contain_text("XFA-compatible")
        files.select_option("cleaned_pdf_3")
        tab("configure")
        expect(pages).to_have_value("1-3,8")

        visit(FACTORY)
        upload(upload_bytes("same-name.txt", "text/plain", first_text.encode()))
        expect(original.locator("pre")).to_have_text(first_text)
        first_id = files.input_value()
        open_settings(page)
        page.locator('[data-setting="response"]').select_option("json")
        action("run").click()
        expect(page.locator(".ingest-output")).to_contain_text("The complete second line.")
        upload(upload_bytes("same-name.txt", "text/plain", second_text.encode()))
        expect(original.locator("pre")).to_have_text(second_text)
        second_id = files.input_value()
        assert second_id != first_id
        expect(page.locator('[data-setting="response"]')).to_have_value("text")
        url = page.locator("#original-download a").get_attribute("href")
        action("remove").click()
        expect(page.locator(f'[data-file-id="{first_id}"]')).to_be_focused()
        assert page.evaluate("async url => fetch(url).then(() => false, () => true)", url)
        open_file(first_id)
        tab("configure")
        expect(page.locator('[data-setting="response"]')).to_have_value("json")
        upload(upload_bytes("same-name.txt", "text/plain", second_text.encode()))
        expect(original.locator("pre")).to_have_text(second_text)
        assert files.input_value() not in (first_id, second_id)
        visit(FINANCE)
        expect(page.locator(".file-open").filter(has_text="same-name.txt")).to_have_count(0)

        upload(audio_fixture())
        expect(original.locator("audio")).to_be_visible()
        page.wait_for_function("document.querySelector('audio')?.readyState >= 1")
        open_settings(page)
        page.locator('[data-setting="language"]').select_option("en")
        original.locator("audio").evaluate("item => item.dataset.playbackCheck = 'retained'")
        action("prepare").click()
        expect(original.locator("audio")).to_have_attribute("data-playback-check", "retained")
        expect(page.locator(".ingest-output")).to_contain_text('"language": "en"')
        upload(video_fixture(page))
        expect(original.locator("video")).to_be_visible()
        page.wait_for_function("document.querySelector('video')?.videoWidth > 0")
        files.select_option(label="recording.wav")
        tab("configure")
        expect(page.locator('[data-setting="language"]')).to_have_value("en")
        page.reload()
        expect(original).to_contain_text("This local file is no longer available in this tab")
        page.get_by_role("link", name="Back to files").click()
        expect(page.locator(".file-library")).to_be_visible()

        visit(FINANCE, "jade/reported-payment-concern")
        expect(original.locator("pre")).to_have_text(JADE.read_text().strip())
        expect(page.locator(".ingest-results blockquote")).to_contain_text("opaque cross-border payment flows")
        tab("configure")
        expect(page.locator('[data-ingest-tab="configure"]')).to_have_attribute("aria-pressed", "true")
        page.route("**/api/original?*", lambda route: route.fulfill(status=410, json={"error": "Fixture changed"}))
        visit(FINANCE, "cleaned_pdf_3")
        expect(original).to_contain_text("The pinned PDF could not be read")
        expect(original.locator("object")).to_have_count(0)
        check_batch_prompts(page, base)
        assert not errors, errors
        browser.close()


def open_settings(page):
    details = page.locator("details.ingest-options")
    expect(details).to_have_count(1)
    if details.get_attribute("open") is None:
        details.locator("> summary").click()


def check_batch_prompts(page, base):
    """The upload has no prompt. Each file keeps its own prompt under the processing settings disclosure."""
    file_prompt = page.locator('[data-setting="prompt"]')
    file_picker = page.locator("#workspace-file")
    dropzone = page.locator("[data-upload-dropzone]")
    results = page.locator(".ingest-output")
    source_text = "The first line.\nThe second line."

    def back():
        page.get_by_role("link", name="Back to files").click()
        expect(page.locator(".file-library")).to_be_visible()

    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        for dataset in (FINANCE, FACTORY):
            page.goto(base + f"#{dataset}/ingest")
            expect(page.locator(".breadcrumb")).to_contain_text(
                "Financial Services" if dataset == FINANCE else "Manufacturing"
            )
            expect(dropzone).to_be_visible()
            expect(page.locator("#upload-prompt")).to_have_count(0)
            expect(page.get_by_text("Extraction instructions")).to_have_count(0)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            left = page.locator(".file-library-list").bounding_box()
            right = page.locator(".file-library-upload").bounding_box()
            assert left["x"] < right["x"] if width > 760 else left["y"] < right["y"]
            later = f"Retain timestamps for {dataset} at {width}."

            label = f"batch-{width}"
            file_picker.focus()
            with page.expect_file_chooser() as chooser:
                page.keyboard.press("Enter")
            chooser.value.set_files([
                upload_bytes(label + ".png", "image/png", png_fixture()),
                upload_bytes(label + ".txt", "text/plain", source_text.encode()),
            ])
            expect(page.locator(".file-added .file-open")).to_have_count(2)
            page.get_by_role("link", name=label + ".png", exact=True).click()
            # Settings sit under one collapsed disclosure with defaults.
            expect(page.locator("details.ingest-options")).to_have_count(1)
            assert page.locator("details.ingest-options").get_attribute("open") is None
            expect(file_prompt).to_be_hidden()
            open_settings(page)
            expect(file_prompt).to_have_value("")
            expect(file_prompt).to_have_attribute("maxlength", "2000")
            file_prompt.fill("A file-specific instruction.")
            page.get_by_role("button", name="Prepare request").click()
            expect(results).to_contain_text('"prompt": "A file-specific instruction."')
            back()
            page.get_by_role("link", name=label + ".txt", exact=True).click()
            expect(page.locator(".ingest-processing")).to_contain_text("Read the selected text file in this browser")
            open_settings(page)
            expect(file_prompt).to_have_value("")
            page.get_by_role("button", name="Run", exact=True).click()
            expect(results).to_have_text(source_text)
            back()
            audio = audio_fixture()
            page.evaluate("""({bytes, name}) => {
              const transfer = new DataTransfer();
              transfer.items.add(new File([new Uint8Array(bytes)], name, {type:'audio/wav'}));
              document.querySelector('[data-upload-dropzone]').dispatchEvent(
                new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:transfer})
              );
            }""", {"bytes": list(audio["buffer"]), "name": label + ".wav"})
            expect(page.locator(".file-added .file-open")).to_be_focused()
            page.get_by_role("link", name=label + ".wav", exact=True).click()
            open_settings(page)
            file_prompt.fill(later)
            page.get_by_role("button", name="Prepare request").click()
            expect(results).to_contain_text(later)
            expect(results).not_to_contain_text("A file-specific instruction.")
            back()
            page.get_by_role("button", name="Documents", exact=True).click()
            page.get_by_role("link", name=label + ".png", exact=True).click()
            page.get_by_role("button", name="Configure", exact=True).click()
            open_settings(page)
            expect(file_prompt).to_have_value("A file-specific instruction.")


if __name__ == "__main__":
    endpoint = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8137/").rstrip("/") + "/"
    exercise(endpoint)
    print("Ingest verified at 1440, 980 and 390 pixels.")
