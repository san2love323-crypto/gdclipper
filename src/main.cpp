// GD Clipper v2.1 - standalone Geode mod (no companion app)
// While you are inside a level, frames are recorded into 2-second segments by an ffmpeg child process.
// Capture methods: ingame (reads the game framebuffer, default), ddagrab / gdigrab (grab the desktop).
// On a clear:  cut version (winning attempt)  +  full version (level enter -> clear).   Quit without a clear: discarded.
#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/CCEGLView.hpp>
#include <windows.h>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <deque>
#include <memory>
#include <thread>
#include <chrono>
#include <string>
#include <vector>
#include <fstream>
#include <filesystem>
#include <cstdio>
#include <ctime>
#include <cstdint>

using namespace geode::prelude;
namespace fs = std::filesystem;

namespace clip {

constexpr double SEG = 2.0;                       // segment length, seconds

static std::mutex g_mx;
static HANDLE g_ff = nullptr;                     // recording ffmpeg process
static HANDLE g_job = nullptr;                    // kills ffmpeg when the game exits
static std::atomic<double> g_t0{0.0};             // QPC time of the first recorded frame (0 = not yet known)
static std::atomic<double> g_attempt{0.0};        // QPC time the current attempt started
static std::atomic<int> g_session{0};             // bumped on every start/stop, so stale threads can tell
static std::atomic<bool> g_saving{false};
static std::atomic<bool> g_stopAfterSave{false};
static std::atomic<bool> g_ffReady{false};
static std::atomic<bool> g_ffDownloading{false};
static std::atomic<bool> g_usedFallback{false};

static double now() {
    LARGE_INTEGER f, c;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart / (double)f.QuadPart;
}

static void notify(std::string msg, bool ok = true) {
    Loader::get()->queueInMainThread([msg, ok] {
        Notification::create(msg, ok ? NotificationIcon::Success : NotificationIcon::Error)->show();
    });
}

static fs::path saveDir()   { return Mod::get()->getSaveDir(); }
static fs::path bufDir()    { return saveDir() / "buffer"; }
static fs::path ffmpegExe() { return saveDir() / "ffmpeg.exe"; }

static std::wstring widen(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), w.data(), n);
    return w;
}

static fs::path outDir() {
    auto s = Mod::get()->getSettingValue<std::string>("out-dir");
    if (!s.empty()) return fs::path(widen(s));
    const wchar_t* home = _wgetenv(L"USERPROFILE");
    return fs::path(home ? home : L"C:\\") / L"Videos" / L"GD Clips";
}

static std::wstring segName(int i) {
    wchar_t b[32];
    swprintf(b, 32, L"seg%06d.mp4", i);
    return b;
}

// ---- process helpers -------------------------------------------------------------------------------
// Runs a command line hidden. wait=true blocks and returns the exit code; wait=false returns the process handle in *out.
static bool run(std::wstring cmd, const fs::path& cwd, bool wait, HANDLE* out = nullptr, DWORD* exitCode = nullptr) {
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    std::wstring dir = cwd.wstring();
    if (!CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, dir.c_str(), &si, &pi)) {
        log::error("CreateProcess failed: {}", GetLastError());
        return false;
    }
    CloseHandle(pi.hThread);
    if (g_job) AssignProcessToJobObject(g_job, pi.hProcess);
    if (wait) {
        WaitForSingleObject(pi.hProcess, 15 * 60 * 1000);
        DWORD code = 1;
        GetExitCodeProcess(pi.hProcess, &code);
        if (exitCode) *exitCode = code;
        CloseHandle(pi.hProcess);
        return code == 0;
    }
    if (out) *out = pi.hProcess; else CloseHandle(pi.hProcess);
    return true;
}

static std::wstring q(const fs::path& p) { return L"\"" + p.wstring() + L"\""; }

// ---- ffmpeg download (first run) -------------------------------------------------------------------
static void ensureFfmpeg() {
    std::error_code ec;
    fs::create_directories(saveDir(), ec);
    if (fs::exists(ffmpegExe(), ec)) { g_ffReady = true; return; }
    // reuse the copy the old GD Clipper app downloaded, if there is one
    if (const wchar_t* appdata = _wgetenv(L"APPDATA")) {
        fs::path old = fs::path(appdata) / L"GDClipper" / L"ffmpeg.exe";
        if (fs::exists(old, ec)) {
            fs::copy_file(old, ffmpegExe(), fs::copy_options::overwrite_existing, ec);
            if (!ec) { g_ffReady = true; return; }
        }
    }
    if (g_ffDownloading.exchange(true)) return;
    std::thread([] {
        notify("GD Clipper: downloading ffmpeg (90MB)...");
        std::wstring ps =
            L"powershell -NoProfile -ExecutionPolicy Bypass -Command \""
            L"$ProgressPreference='SilentlyContinue';"
            L"[Net.ServicePointManager]::SecurityProtocol='Tls12';"
            L"Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'ff.zip';"
            L"Expand-Archive 'ff.zip' 'ffx' -Force;"
            L"Copy-Item (Get-ChildItem 'ffx' -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName 'ffmpeg.exe';"
            L"Remove-Item 'ff.zip'; Remove-Item 'ffx' -Recurse\"";
        run(ps, saveDir(), true);
        std::error_code ec;
        if (fs::exists(ffmpegExe(), ec)) { g_ffReady = true; notify("GD Clipper: ffmpeg ready"); }
        else notify("GD Clipper: ffmpeg download failed", false);
        g_ffDownloading = false;
    }).detach();
}

// ---- game window rectangle (physical pixels) --------------------------------------------------------
struct Rect { int x, y, w, h; };

static BOOL CALLBACK enumProc(HWND h, LPARAM lp) {
    DWORD pid = 0;
    GetWindowThreadProcessId(h, &pid);
    if (pid == GetCurrentProcessId() && IsWindowVisible(h) && GetWindow(h, GW_OWNER) == nullptr) {
        RECT r;
        if (GetClientRect(h, &r) && r.right - r.left >= 200) { *(HWND*)lp = h; return FALSE; }
    }
    return TRUE;
}

static bool windowRect(Rect& out) {
    auto old = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);   // real pixels even with 125% scaling
    HWND hwnd = nullptr;
    EnumWindows(enumProc, (LPARAM)&hwnd);
    bool ok = false;
    if (hwnd && !IsIconic(hwnd)) {
        RECT r;
        POINT p{0, 0};
        GetClientRect(hwnd, &r);
        ClientToScreen(hwnd, &p);
        int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
        int x0 = p.x < 0 ? 0 : p.x, y0 = p.y < 0 ? 0 : p.y;
        int x1 = p.x + (r.right - r.left), y1 = p.y + (r.bottom - r.top);
        if (x1 > sw) x1 = sw;
        if (y1 > sh) y1 = sh;
        int w = (x1 - x0) & ~1, h = (y1 - y0) & ~1;               // even size for yuv420p
        if (w >= 200 && h >= 200) { out = {x0, y0, w, h}; ok = true; }
    }
    if (old) SetThreadDpiAwarenessContext(old);
    return ok;
}

// ---- in-game capture: read the game's own framebuffer and pipe raw frames to ffmpeg ----------------
// Works in exclusive fullscreen, on any monitor / GPU, with any DPI scaling, because nothing is grabbed from the desktop.
struct Grab {
    HANDLE pipe = nullptr;                        // write end of ffmpeg's stdin
    int w = 0, h = 0;
    double interval = 1.0 / 60.0, last = 0.0;
    std::atomic<bool> alive{true};
    std::mutex mx;
    std::condition_variable cv;
    std::deque<std::vector<uint8_t>> q;           // frames waiting to be written (max 2, extra frames are dropped)
    std::vector<std::vector<uint8_t>> pool;       // reused buffers
};
static std::mutex g_grabMx;
static std::shared_ptr<Grab> g_grab;

static void grabWriter(std::shared_ptr<Grab> g) {
    for (;;) {
        std::vector<uint8_t> buf;
        {
            std::unique_lock lk(g->mx);
            g->cv.wait(lk, [&] { return !g->q.empty() || !g->alive; });
            if (!g->alive) break;
            buf = std::move(g->q.front());
            g->q.pop_front();
        }
        size_t off = 0;
        while (off < buf.size() && g->alive) {
            DWORD chunk = (DWORD)((buf.size() - off) > (4u << 20) ? (4u << 20) : (buf.size() - off)), w = 0;
            if (!WriteFile(g->pipe, buf.data() + off, chunk, &w, nullptr) || w == 0) { g->alive = false; break; }
            off += w;
        }
        std::lock_guard lk(g->mx);
        if (g->pool.size() < 3) g->pool.push_back(std::move(buf));
    }
    CloseHandle(g->pipe);
}

// called on the main thread right before every buffer swap
static void grabFrame() {
    std::shared_ptr<Grab> g;
    {
        std::lock_guard lk(g_grabMx);
        g = g_grab;
    }
    if (!g || !g->alive) return;
    double t = now();
    if (t - g->last < g->interval) return;
    g->last = (t - g->last > 4 * g->interval) ? t : g->last + g->interval;
    auto view = CCDirector::sharedDirector()->getOpenGLView();
    if (!view) return;
    auto size = view->getFrameSize();
    if ((int)size.width < g->w || (int)size.height < g->h) return;      // window got smaller: skip (ffmpeg repeats the last frame)
    std::vector<uint8_t> buf;
    {
        std::lock_guard lk(g->mx);
        if (g->q.size() >= 2) return;                                    // encoder is behind: drop this frame
        if (!g->pool.empty()) { buf = std::move(g->pool.back()); g->pool.pop_back(); }
    }
    buf.resize((size_t)g->w * g->h * 4);
    GLint oldAlign = 4;
    glGetIntegerv(GL_PACK_ALIGNMENT, &oldAlign);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, g->w, g->h, GL_RGBA, GL_UNSIGNED_BYTE, buf.data());
    glPixelStorei(GL_PACK_ALIGNMENT, oldAlign);
    {
        std::lock_guard lk(g->mx);
        g->q.push_back(std::move(buf));
    }
    g->cv.notify_one();
}

static void stopGrab() {
    std::shared_ptr<Grab> g;
    {
        std::lock_guard lk(g_grabMx);
        g = std::move(g_grab);
        g_grab = nullptr;
    }
    if (g) { g->alive = false; g->cv.notify_all(); }
}

// must be called on the main thread (reads the GL view size)
static bool launchIngame(int fps) {
    auto view = CCDirector::sharedDirector()->getOpenGLView();
    if (!view) return false;
    auto size = view->getFrameSize();
    int w = ((int)size.width) & ~1, h = ((int)size.height) & ~1;
    if (w < 200 || h < 200) return false;
    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof sa;
    sa.bInheritHandle = TRUE;
    HANDLE rd = nullptr, wr = nullptr;
    if (!CreatePipe(&rd, &wr, &sa, 32u << 20)) return false;
    SetHandleInformation(wr, HANDLE_FLAG_INHERIT, 0);                    // only the read end goes to ffmpeg
    HANDLE nul = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, nullptr);
    wchar_t in[512];
    swprintf(in, 512, L" -hide_banner -loglevel error -y -use_wallclock_as_timestamps 1 -f rawvideo -pix_fmt rgba -video_size %dx%d -framerate %d -i pipe:0"
                      L" -vf vflip,setpts=PTS-STARTPTS -fps_mode cfr -r %d", w, h, fps, fps);
    std::wstring cmd = q(ffmpegExe()) + in +
        L" -c:v libx264 -preset ultrafast -crf 20 -pix_fmt yuv420p -force_key_frames \"expr:gte(t,n_forced*2)\""
        L" -f segment -segment_time 2 -reset_timestamps 1 seg%06d.mp4";
    STARTUPINFOW si{};
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = rd;
    si.hStdOutput = nul;
    si.hStdError = nul;
    PROCESS_INFORMATION pi{};
    std::wstring dir = bufDir().wstring();
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, dir.c_str(), &si, &pi);
    CloseHandle(rd);
    if (nul != INVALID_HANDLE_VALUE) CloseHandle(nul);
    if (!ok) { log::error("CreateProcess (ingame) failed: {}", GetLastError()); CloseHandle(wr); return false; }
    CloseHandle(pi.hThread);
    if (g_job) AssignProcessToJobObject(g_job, pi.hProcess);
    g_ff = pi.hProcess;
    auto g = std::make_shared<Grab>();
    g->pipe = wr; g->w = w; g->h = h; g->interval = 1.0 / fps;
    {
        std::lock_guard lk(g_grabMx);
        g_grab = g;
    }
    std::thread(grabWriter, g).detach();
    log::info("recording started ({}x{} @{}fps, ingame)", w, h, fps);
    return true;
}

// ---- recorder -------------------------------------------------------------------------------------
static void clearBuffer() {
    std::error_code ec;
    fs::remove_all(bufDir(), ec);
    fs::create_directories(bufDir(), ec);
}

static bool launchRecorder(const std::string& method) {
    int fps = (int)Mod::get()->getSettingValue<int64_t>("fps");
    fps = fps >= 60 ? 60 : 30;
    if (method == "ingame") return launchIngame(fps);
    Rect r;
    if (!windowRect(r)) { log::warn("game window not found"); return false; }
    wchar_t in[512];
    if (method == "gdigrab")
        swprintf(in, 512, L"-f gdigrab -framerate %d -draw_mouse 0 -offset_x %d -offset_y %d -video_size %dx%d -i desktop", fps, r.x, r.y, r.w, r.h);
    else
        swprintf(in, 512, L"-f lavfi -i ddagrab=output_idx=0:framerate=%d:draw_mouse=0:video_size=%dx%d:offset_x=%d:offset_y=%d,hwdownload,format=bgra", fps, r.w, r.h, r.x, r.y);
    std::wstring cmd = q(ffmpegExe()) + L" -nostdin -hide_banner -loglevel error -y " + in +
        L" -c:v libx264 -preset ultrafast -crf 20 -pix_fmt yuv420p -force_key_frames \"expr:gte(t,n_forced*2)\""
        L" -f segment -segment_time 2 -reset_timestamps 1 seg%06d.mp4";
    HANDLE h = nullptr;
    if (!run(cmd, bufDir(), false, &h)) return false;
    g_ff = h;
    log::info("recording started ({}x{} @{}fps, {})", r.w, r.h, fps, method);
    return true;
}

static void stopLocked() {
    ++g_session;
    stopGrab();
    if (g_ff) { TerminateProcess(g_ff, 0); WaitForSingleObject(g_ff, 2000); CloseHandle(g_ff); g_ff = nullptr; }
    g_t0 = 0.0;
}

static void stop(bool discard) {
    std::lock_guard lk(g_mx);
    stopLocked();
    if (discard) clearBuffer();
}

static void start() {
    if (!Mod::get()->getSettingValue<bool>("enabled")) return;
    if (!g_ffReady) { ensureFfmpeg(); if (!g_ffReady) return; }
    if (g_saving) return;                                    // still writing the previous clip
    std::lock_guard lk(g_mx);
    stopLocked();
    clearBuffer();
    std::string method = Mod::get()->getSettingValue<std::string>("capture-method");
    if (g_usedFallback) method = "gdigrab";
    if (!launchRecorder(method)) { notify("GD Clipper: could not start recording", false); return; }
    int session = g_session.load();
    // watcher: learns when the first frame was written, falls back to gdigrab if ddagrab dies, trims the buffer
    std::thread([session, method] {
        std::error_code ec;
        double launched = now();
        bool announced = false;
        while (g_session.load() == session) {
            std::this_thread::sleep_for(std::chrono::milliseconds(g_t0.load() == 0.0 ? 20 : 2000));
            if (g_session.load() != session) return;
            if (g_t0.load() == 0.0) {
                if (fs::exists(bufDir() / segName(0), ec)) {
                    g_t0 = now();
                    if (!announced) { notify("GD Clipper: recording"); announced = true; }
                } else if (now() - launched > 4.0) {
                    std::lock_guard lk(g_mx);
                    if (g_session.load() != session) return;
                    DWORD code = STILL_ACTIVE;
                    if (g_ff) GetExitCodeProcess(g_ff, &code);
                    if (code != STILL_ACTIVE && method != "gdigrab") {          // this capture method does not work here
                        log::warn("{} failed (exit {}), falling back to gdigrab", method, code);
                        stopGrab();
                        CloseHandle(g_ff); g_ff = nullptr;
                        g_usedFallback = true;
                        clearBuffer();
                        if (!launchRecorder("gdigrab")) { notify("GD Clipper: recording failed", false); return; }
                        launched = now();
                    } else if (code != STILL_ACTIVE) {
                        notify("GD Clipper: recording failed", false);
                        return;
                    }
                }
                continue;
            }
            if (g_saving) continue;
            // drop segments older than the buffer limit
            int keep = (int)(Mod::get()->getSettingValue<int64_t>("buffer-minutes") * 60 / SEG);
            int newest = (int)((now() - g_t0.load()) / SEG);
            for (int i = newest - keep - 30; i < newest - keep; ++i)
                if (i >= 0) fs::remove(bufDir() / segName(i), ec);
        }
    }).detach();
}

// ---- saving ---------------------------------------------------------------------------------------
static bool writeList(const fs::path& list, int i0, int i1, int* count) {
    std::error_code ec;
    std::ofstream f(list);
    int n = 0;
    for (int i = i0; i <= i1; ++i) {
        if (!fs::exists(bufDir() / segName(i), ec)) continue;
        char b[64];
        snprintf(b, sizeof b, "file 'seg%06d.mp4'\n", i);      // relative to the list file: no path encoding issues
        f << b;
        ++n;
    }
    if (count) *count = n;
    return n > 0;
}

static void save(double tAttempt, double tComplete, int session) {
    g_saving = true;
    double pre = Mod::get()->getSettingValue<double>("pre");
    double post = Mod::get()->getSettingValue<double>("post");
    bool full = Mod::get()->getSettingValue<bool>("save-full");
    int fps = (int)Mod::get()->getSettingValue<int64_t>("fps") >= 60 ? 60 : 30;
    std::error_code ec;
    bool okCut = false, okFull = false;
    double t0 = g_t0.load();
    if (t0 > 0.0 && g_session.load() == session) {
        double a = tAttempt - pre - t0, b = tComplete + post - t0;
        if (a < 0) a = 0;
        int i0 = (int)(a / SEG), i1 = (int)(b / SEG);
        // wait until the segment containing the end has been closed (the next one exists)
        for (int k = 0; k < 150 && g_session.load() == session; ++k) {
            if (fs::exists(bufDir() / segName(i1 + 1), ec)) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        fs::path dir = outDir();
        fs::create_directories(dir, ec);
        std::time_t tt = std::time(nullptr);
        std::tm tm{};
        localtime_s(&tm, &tt);
        wchar_t stamp[32];
        wcsftime(stamp, 32, L"%Y%m%d_%H%M%S", &tm);
        fs::path cut = dir / (std::wstring(L"GD_clear_") + stamp + L"_cut.mp4");
        fs::path fullOut = dir / (std::wstring(L"GD_clear_") + stamp + L"_full.mp4");

        // cut version: precise trim, re-encoded
        if (writeList(bufDir() / "cut.txt", i0, i1, nullptr)) {
            wchar_t opt[256];
            swprintf(opt, 256, L" -ss %.3f -t %.3f -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p -r %d -movflags +faststart ", a - i0 * SEG, b - a, fps);
            std::wstring cmd = q(ffmpegExe()) + L" -nostdin -hide_banner -loglevel error -y -f concat -safe 0 -i cut.txt" + opt + q(cut);
            okCut = run(cmd, bufDir(), true);
        }
        // full version: stream copy of everything still in the buffer (fast)
        if (full) {
            int first = 0;
            while (first < i1 && !fs::exists(bufDir() / segName(first), ec)) ++first;
            if (writeList(bufDir() / "full.txt", first, i1, nullptr)) {
                std::wstring cmd = q(ffmpegExe()) + L" -nostdin -hide_banner -loglevel error -y -f concat -safe 0 -i full.txt -c copy -movflags +faststart " + q(fullOut);
                okFull = run(cmd, bufDir(), true);
            }
        }
        log::info("saved: cut={} full={}", okCut, okFull);
    }
    if (okCut) notify(full && okFull ? "GD Clipper: saved cut + full" : "GD Clipper: clip saved");
    else notify("GD Clipper: save failed", false);
    g_saving = false;
    if (g_stopAfterSave.exchange(false)) stop(true);            // the player left the level while we were saving
}

static void onComplete(bool practice) {
    if (practice && !Mod::get()->getSettingValue<bool>("save-practice")) return;
    if (g_t0.load() == 0.0 || g_saving) return;
    double tA = g_attempt.load(), tC = now();
    if (tA == 0.0 || tA > tC) tA = tC - 30.0;
    int session = g_session.load();
    notify("GD Clipper: saving clip...");
    std::thread([tA, tC, session] { save(tA, tC, session); }).detach();
}

static void onQuit() {
    if (g_saving) { g_stopAfterSave = true; return; }
    stop(true);
}

} // namespace clip

$on_mod(Loaded) {
    clip::g_job = CreateJobObjectW(nullptr, nullptr);
    if (clip::g_job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION info{};
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;   // ffmpeg dies with the game
        SetInformationJobObject(clip::g_job, JobObjectExtendedLimitInformation, &info, sizeof info);
    }
    clip::ensureFfmpeg();
}

class $modify(ClipGLView, CCEGLView) {
    void swapBuffers() {
        clip::grabFrame();                 // copy the finished frame before it is shown
        CCEGLView::swapBuffers();
    }
};

class $modify(ClipPlayLayer, PlayLayer) {
    bool init(GJGameLevel* level, bool useReplay, bool dontCreateObjects) {
        if (!PlayLayer::init(level, useReplay, dontCreateObjects)) return false;
        clip::g_attempt = clip::now();
        clip::start();
        return true;
    }

    void resetLevel() {
        PlayLayer::resetLevel();
        clip::g_attempt = clip::now();
    }

    void levelComplete() {
        bool practice = m_isPracticeMode;
        PlayLayer::levelComplete();
        clip::onComplete(practice);
    }

    void onQuit() {
        clip::onQuit();
        PlayLayer::onQuit();
    }
};
