// GD Clipper - Geode mod
// Reports PlayLayer events to the GD Clipper recorder app over a named pipe.
// Line format:  "<EVENT> <qpc seconds> <practice 0|1>\n"   (same protocol as the old injected hook DLL)
#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <windows.h>
#include <atomic>
#include <thread>
#include <chrono>
#include <cstdio>
#include <cstring>

using namespace geode::prelude;

namespace clip {
    static std::atomic<HANDLE> g_pipe{INVALID_HANDLE_VALUE};
    static std::atomic<bool> g_practice{false};

    static bool connect() {
        if (g_pipe.load() != INVALID_HANDLE_VALUE) return true;
        HANDLE h = CreateFileA("\\\\.\\pipe\\gdclipper", GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
        if (h == INVALID_HANDLE_VALUE) return false;
        g_pipe.store(h);
        const char* hello = "HOOKED 0 0\n";
        DWORD w = 0;
        WriteFile(h, hello, (DWORD)strlen(hello), &w, nullptr);
        log::info("connected to GD Clipper app");
        return true;
    }

    static void send(const char* ev) {
        if (!connect()) return;                       // app not running: nothing to do
        LARGE_INTEGER f, c;
        QueryPerformanceFrequency(&f);
        QueryPerformanceCounter(&c);
        double t = (double)c.QuadPart / (double)f.QuadPart;
        char buf[160];
        int n = snprintf(buf, sizeof buf, "%s %.4f %d\n", ev, t, g_practice.load() ? 1 : 0);
        DWORD w = 0;
        HANDLE h = g_pipe.load();
        if (!WriteFile(h, buf, (DWORD)n, &w, nullptr)) {   // app closed: drop the handle, reconnect later
            CloseHandle(h);
            g_pipe.store(INVALID_HANDLE_VALUE);
        }
    }

    static bool connected() { return g_pipe.load() != INVALID_HANDLE_VALUE; }
}

// The app may be started after the game: keep trying to connect in the background.
$on_mod(Loaded) {
    std::thread([] {
        for (;;) {
            clip::connect();
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
    }).detach();
}

class $modify(ClipPlayLayer, PlayLayer) {
    bool init(GJGameLevel* level, bool useReplay, bool dontCreateObjects) {
        clip::g_practice = false;
        if (!PlayLayer::init(level, useReplay, dontCreateObjects)) return false;
        clip::send("LEVEL_ENTER");
        return true;
    }

    void resetLevel() {
        PlayLayer::resetLevel();
        clip::g_practice = m_isPracticeMode;
        clip::send("ATTEMPT");
    }

    void destroyPlayer(PlayerObject* player, GameObject* object) {
        PlayLayer::destroyPlayer(player, object);
        if (object == m_anticheatSpike) return;       // fake death the game triggers at level start
        clip::send("DEATH");
    }

    void levelComplete() {
        clip::g_practice = m_isPracticeMode;
        PlayLayer::levelComplete();
        clip::send("COMPLETE");
        if (clip::connected() && !m_isPracticeMode)
            Notification::create("GD Clipper: saving clip", NotificationIcon::Success)->show();
    }

    void pauseGame(bool unfocused) {
        PlayLayer::pauseGame(unfocused);
        clip::send("PAUSE");
    }

    void resume() {
        PlayLayer::resume();
        clip::send("RESUME");
    }

    void togglePracticeMode(bool practice) {
        clip::g_practice = practice;
        PlayLayer::togglePracticeMode(practice);
        clip::send(practice ? "PRACTICE_ON" : "PRACTICE_OFF");
    }

    void onQuit() {
        clip::send("QUIT");
        PlayLayer::onQuit();
    }
};
