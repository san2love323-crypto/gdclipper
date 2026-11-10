// Icon Painter — draw your own cube icon inside Geometry Dash (Geode mod, GD 2.2081, Windows)
//
// Layers (same stacking the game uses for icons):
//   L2 (bottom)  = tinted with player color 2
//   L1 (middle)  = tinted with player color 1
//   L3 (top)     = fixed colors, never tinted  (outlines, eyes, imported photos)
//
// Only cocos2d-x + GD classes are used for UI (no Geode UI helpers), so it stays buildable across Geode versions.
#include <Geode/Geode.hpp>
#include <Geode/modify/GJGarageLayer.hpp>
#include <Geode/modify/SimplePlayer.hpp>
#include <Geode/modify/PlayerObject.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <atomic>
#include <string>
#include <thread>
#include <vector>

#ifdef GEODE_IS_WINDOWS
#include <Windows.h>
#include <commdlg.h>
#include <objbase.h>
#include <wincodec.h>
#endif

#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_JPEG
#define STBI_ONLY_PNG
#define STBI_ONLY_BMP
#define STBI_NO_STDIO
#include "stb_image.h"

using namespace geode::prelude;

// =====================================================================================
// Icon data
// =====================================================================================
namespace ip {

constexpr int N = 32;            // canvas is N x N pixels
constexpr int LAYERS = 3;        // 0 = L1 (color 1), 1 = L2 (color 2), 2 = L3 (fixed)
constexpr float ICON_UNITS = 30.f;

struct Px { uint8_t r = 0, g = 0, b = 0, a = 0; };
using Layer = std::array<Px, N * N>;
using Icon = std::array<Layer, LAYERS>;

Icon g_icon{};                   // the saved icon
bool g_hasIcon = false;
bool g_enabled = false;

// sprite frames built from g_icon (null until built)
CCSpriteFrame* g_frame[LAYERS] = {nullptr, nullptr, nullptr};
CCSpriteFrame* g_glowFrame = nullptr;
float g_builtScale = 0.f;

// ---------- save / load (hex string in the mod's saved values) ----------
std::string toHex(Icon const& ic) {
    static const char* H = "0123456789abcdef";
    std::string s; s.reserve(LAYERS * N * N * 8);
    for (auto const& l : ic) for (auto const& p : l)
        for (uint8_t v : {p.r, p.g, p.b, p.a}) { s.push_back(H[v >> 4]); s.push_back(H[v & 15]); }
    return s;
}
bool fromHex(std::string const& s, Icon& ic) {
    if (s.size() != (size_t)LAYERS * N * N * 8) return false;
    auto hv = [](char c) -> int { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : 0; };
    size_t i = 0;
    for (auto& l : ic) for (auto& p : l) {
        uint8_t v[4];
        for (auto& x : v) { x = (uint8_t)(hv(s[i]) * 16 + hv(s[i + 1])); i += 2; }
        p = {v[0], v[1], v[2], v[3]};
    }
    return true;
}
void load() {
    g_enabled = Mod::get()->getSavedValue<bool>("enabled", false);
    g_hasIcon = fromHex(Mod::get()->getSavedValue<std::string>("icon", ""), g_icon);
}
void save() {
    Mod::get()->setSavedValue<std::string>("icon", toHex(g_icon));
    Mod::get()->setSavedValue<bool>("enabled", g_enabled);
    g_hasIcon = true;
}

// ---------- textures ----------
CCTexture2D* makeTexture(std::vector<uint8_t> const& rgba, int w, int h) {
    auto tex = new CCTexture2D();
    tex->initWithData(rgba.data(), kCCTexture2DPixelFormat_RGBA8888, w, h, CCSize((float)w, (float)h));
    tex->setAliasTexParameters();   // crisp pixels
    tex->autorelease();
    return tex;
}
CCSpriteFrame* makeFrame(std::vector<uint8_t> const& rgba, int w, int h) {
    auto tex = makeTexture(rgba, w, h);
    auto sz = tex->getContentSize();
    auto fr = CCSpriteFrame::createWithTexture(tex, CCRect(0, 0, sz.width, sz.height));
    fr->retain();
    return fr;
}

// Build the 3 layer frames + a glow frame from g_icon, sized like a real cube (30 units).
void buildFrames() {
    for (auto& f : g_frame) { CC_SAFE_RELEASE_NULL(f); }
    CC_SAFE_RELEASE_NULL(g_glowFrame);
    if (!g_hasIcon) return;

    float csf = CCDirector::sharedDirector()->getContentScaleFactor();
    g_builtScale = csf;
    int T = (std::max)(N, (int)std::lround(ICON_UNITS * csf));   // texture pixels per side

    for (int l = 0; l < LAYERS; l++) {
        std::vector<uint8_t> buf((size_t)T * T * 4);
        for (int y = 0; y < T; y++) for (int x = 0; x < T; x++) {
            auto const& p = g_icon[l][(y * N / T) * N + (x * N / T)];   // nearest neighbour upscale
            uint8_t* o = &buf[((size_t)y * T + x) * 4];
            o[0] = p.r; o[1] = p.g; o[2] = p.b; o[3] = p.a;
        }
        g_frame[l] = makeFrame(buf, T, T);
    }

    // glow = white silhouette of all layers, grown by ~1.5 units, on a slightly bigger texture
    int pad = (std::max)(2, (int)std::ceil(1.5f * csf));
    int G = T + pad * 2;
    std::vector<uint8_t> solid((size_t)G * G, 0);
    for (int y = 0; y < T; y++) for (int x = 0; x < T; x++) {
        int i = (y * N / T) * N + (x * N / T);
        if (g_icon[0][i].a > 40 || g_icon[1][i].a > 40 || g_icon[2][i].a > 40) solid[(size_t)(y + pad) * G + x + pad] = 1;
    }
    std::vector<uint8_t> glow((size_t)G * G * 4, 0);
    for (int y = 0; y < G; y++) for (int x = 0; x < G; x++) {
        bool on = false;
        for (int dy = -pad; dy <= pad && !on; dy++) for (int dx = -pad; dx <= pad; dx++) {
            if (dx * dx + dy * dy > pad * pad) continue;
            int xx = x + dx, yy = y + dy;
            if (xx >= 0 && yy >= 0 && xx < G && yy < G && solid[(size_t)yy * G + xx]) { on = true; break; }
        }
        if (on) { uint8_t* o = &glow[((size_t)y * G + x) * 4]; o[0] = o[1] = o[2] = o[3] = 255; }
    }
    g_glowFrame = makeFrame(glow, G, G);
}

bool ready() {
    if (!g_enabled || !g_hasIcon) return false;
    float csf = CCDirector::sharedDirector()->getContentScaleFactor();
    if (!g_frame[0] || csf != g_builtScale) buildFrames();   // (re)build lazily, also after a quality change
    return g_frame[0] && g_frame[1] && g_frame[2];
}

void putFrame(CCSprite* spr, CCSpriteFrame* fr, CCNode* centerIn) {
    if (!spr || !fr) return;
    spr->setDisplayFrame(fr);
    spr->setBlendFunc({GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA});
    spr->setVisible(true);
    if (centerIn && spr->getParent() == centerIn) { auto cs = centerIn->getContentSize(); spr->setPosition(CCPoint(cs.width / 2, cs.height / 2)); }
}

// Swap the sprites of an icon:  first = color 1, second = color 2 (behind), detail = fixed colors (front)
void apply(CCSprite* first, CCSprite* second, CCSprite* detail, CCSprite* glow) {
    if (!first) return;
    putFrame(first, g_frame[0], nullptr);
    putFrame(second, g_frame[1], first);
    putFrame(detail, g_frame[2], first);
    if (detail) detail->setColor({255, 255, 255});
    if (glow && g_glowFrame) {
        bool wasVisible = glow->isVisible();
        putFrame(glow, g_glowFrame, first);
        glow->setVisible(wasVisible);       // keep the game's own glow on/off choice
    }
}

} // namespace ip

$on_mod(Loaded) {
    ip::load();
}

// =====================================================================================
// Hooks: put the drawing on the player's cube
// =====================================================================================
class $modify(IPSimplePlayer, SimplePlayer) {
    void updatePlayerFrame(int id, IconType type) {
        SimplePlayer::updatePlayerFrame(id, type);
        // only the cube you currently have selected is replaced (so the rest of the icon kit stays normal)
        if (type != IconType::Cube || id != GameManager::get()->getPlayerFrame()) return;
        if (!ip::ready()) return;
        ip::apply(m_firstLayer, m_secondLayer, m_detailSprite, m_outlineSprite);
    }
};

class $modify(IPPlayerObject, PlayerObject) {
    // true while the player sprite is showing a cube (cube itself, or the cube riding a ship / ufo / jetpack)
    bool showsCube() {
        return !m_isBall && !m_isDart && !m_isSwing && !m_isRobot && !m_isSpider;
    }

    void applyPainted() {
        if (!m_iconSprite || !showsCube() || !ip::ready()) return;
        if (m_iconSprite->isFrameDisplayed(ip::g_frame[0])) return;      // already ours
        ip::apply(m_iconSprite, m_iconSpriteSecondary, m_iconSpriteWhitener, m_iconGlow);
    }

    void updatePlayerFrame(int frame) {
        PlayerObject::updatePlayerFrame(frame);
        applyPainted();
    }

    // The game also swaps the cube frame from places that never go through updatePlayerFrame
    // (level start, respawn, leaving ball / wave / robot ...), so re-check every frame. It is one cheap compare.
    void update(float dt) {
        PlayerObject::update(dt);
        applyPainted();
    }
};

// =====================================================================================
// Editor screen
// =====================================================================================
class IconPainterLayer : public CCLayer {
protected:
    ip::Icon m_icon{};
    std::vector<ip::Icon> m_undo;
    int m_layer = 0;                 // 0..2
    int m_tool = 0;                  // 0 pen, 1 eraser, 2 fill
    int m_brush = 1;                 // 1..3
    ccColor3B m_color = {255, 255, 255};
    bool m_enabled = true;
    bool m_dirty = false;
    bool m_drawing = false;
    int m_lastX = -1, m_lastY = -1;

    CCSprite* m_canvas = nullptr;
    CCSprite* m_preview = nullptr;
    CCLabelBMFont* m_status = nullptr;
    CCLabelBMFont* m_useLabel = nullptr;
    CCMenu* m_palette = nullptr;
    ccColor3B m_col1{}, m_col2{};
    float m_canvasSize = 200.f;
    CCPoint m_canvasPos{};

    // ---------- helpers ----------
    static ccColor3B mul(ip::Px const& p, ccColor3B const& tint) {
        return {(GLubyte)(p.r * tint.r / 255), (GLubyte)(p.g * tint.g / 255), (GLubyte)(p.b * tint.b / 255)};
    }

    // what the icon looks like with the player's colors (L2 under L1 under L3), on a checkerboard
    std::vector<uint8_t> composite(bool checker) {
        using namespace ip;
        std::vector<uint8_t> buf((size_t)N * N * 4);
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++) {
            float r = 0, g = 0, b = 0, a = 0;
            if (checker) { float c = ((x / 2 + y / 2) % 2) ? 70.f : 95.f; r = g = b = c; a = 255; }
            int i = y * N + x;
            const int order[3] = {1, 0, 2};
            for (int l : order) {
                auto const& p = m_icon[l][i];
                if (!p.a) continue;
                ccColor3B c = l == 0 ? mul(p, m_col1) : l == 1 ? mul(p, m_col2) : ccColor3B{p.r, p.g, p.b};
                float pa = p.a / 255.f;
                r = c.r * pa + r * (1 - pa); g = c.g * pa + g * (1 - pa); b = c.b * pa + b * (1 - pa);
                a = (std::max)(a, (float)p.a);
            }
            uint8_t* o = &buf[(size_t)i * 4];
            o[0] = (uint8_t)r; o[1] = (uint8_t)g; o[2] = (uint8_t)b; o[3] = (uint8_t)a;
        }
        return buf;
    }

    void setSpriteTexture(CCSprite* spr, std::vector<uint8_t> const& buf, float size) {
        auto tex = ip::makeTexture(buf, ip::N, ip::N);
        auto sz = tex->getContentSize();
        spr->setTexture(tex);
        spr->setTextureRect(CCRect(0, 0, sz.width, sz.height));
        spr->setBlendFunc({GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA});
        spr->setScale(size / sz.width);
    }

    void redraw() {
        setSpriteTexture(m_canvas, composite(true), m_canvasSize);
        setSpriteTexture(m_preview, composite(false), 34.f);
    }

    void updateStatus() {
        static const char* layerNames[3] = {"L1  (COLOR 1)", "L2  (COLOR 2, BEHIND)", "L3  (FIXED COLORS, FRONT)"};
        static const char* toolNames[3] = {"PEN", "ERASER", "FILL"};
        m_status->setString(fmt::format("{}   |   {}   |   SIZE {}", layerNames[m_layer], toolNames[m_tool], m_brush).c_str());
        m_useLabel->setString(m_enabled ? "USE: ON" : "USE: OFF");
        m_useLabel->setColor(m_enabled ? ccColor3B{120, 255, 120} : ccColor3B{255, 120, 120});
    }

    void pushUndo() {
        m_undo.push_back(m_icon);
        if (m_undo.size() > 30) m_undo.erase(m_undo.begin());
        m_dirty = true;
    }

    void plot(int x, int y) {
        using namespace ip;
        int lo = -(m_brush - 1) / 2, hi = m_brush / 2;
        for (int dy = lo; dy <= hi; dy++) for (int dx = lo; dx <= hi; dx++) {
            int xx = x + dx, yy = y + dy;
            if (xx < 0 || yy < 0 || xx >= N || yy >= N) continue;
            m_icon[m_layer][yy * N + xx] = m_tool == 1 ? Px{} : Px{m_color.r, m_color.g, m_color.b, 255};
        }
    }

    void line(int x0, int y0, int x1, int y1) {   // Bresenham so fast strokes have no gaps
        int dx = std::abs(x1 - x0), dy = -std::abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1, err = dx + dy;
        for (;;) {
            plot(x0, y0);
            if (x0 == x1 && y0 == y1) break;
            int e2 = 2 * err;
            if (e2 >= dy) { err += dy; x0 += sx; }
            if (e2 <= dx) { err += dx; y0 += sy; }
        }
    }

    void fill(int x, int y) {
        using namespace ip;
        auto& L = m_icon[m_layer];
        Px target = L[y * N + x], rep{m_color.r, m_color.g, m_color.b, 255};
        auto same = [](Px const& a, Px const& b) { return a.a == b.a && (a.a == 0 || (a.r == b.r && a.g == b.g && a.b == b.b)); };
        if (same(target, rep)) return;
        std::vector<int> stack{y * N + x};
        while (!stack.empty()) {
            int i = stack.back(); stack.pop_back();
            if (!same(L[i], target)) continue;
            L[i] = rep;
            int cx = i % N, cy = i / N;
            if (cx > 0) stack.push_back(i - 1);
            if (cx < N - 1) stack.push_back(i + 1);
            if (cy > 0) stack.push_back(i - N);
            if (cy < N - 1) stack.push_back(i + N);
        }
    }

    bool touchToPixel(CCTouch* t, int& px, int& py) {
        CCPoint p = t->getLocation();
        float half = m_canvasSize / 2;
        float fx = (p.x - (m_canvasPos.x - half)) / m_canvasSize, fy = (p.y - (m_canvasPos.y - half)) / m_canvasSize;
        if (fx < 0 || fy < 0 || fx >= 1 || fy >= 1) return false;
        px = (int)(fx * ip::N); py = ip::N - 1 - (int)(fy * ip::N);
        return true;
    }

    // ---------- touch ----------
    bool ccTouchBegan(CCTouch* t, CCEvent*) override {
        int x, y;
        if (!touchToPixel(t, x, y)) return false;
        pushUndo();
        if (m_tool == 2) fill(x, y); else plot(x, y);
        m_drawing = m_tool != 2; m_lastX = x; m_lastY = y;
        redraw();
        return true;
    }
    void ccTouchMoved(CCTouch* t, CCEvent*) override {
        int x, y;
        if (!m_drawing || !touchToPixel(t, x, y)) return;
        if (x == m_lastX && y == m_lastY) return;
        line(m_lastX, m_lastY, x, y);
        m_lastX = x; m_lastY = y;
        redraw();
    }
    void ccTouchEnded(CCTouch*, CCEvent*) override { m_drawing = false; }
    void ccTouchCancelled(CCTouch*, CCEvent*) override { m_drawing = false; }
    void registerWithTouchDispatcher() override {
        CCDirector::sharedDirector()->getTouchDispatcher()->addTargetedDelegate(this, 0, true);
    }

    // ---------- palette ----------
    void rebuildPalette() {
        m_palette->removeAllChildren();
        std::vector<ccColor3B> cols;
        if (m_layer < 2) {
            // tinted layers: you only choose how bright the area is; the hue comes from your player color
            cols = {{255, 255, 255}, {200, 200, 200}, {150, 150, 150}, {100, 100, 100}};
        } else {
            cols = {{0, 0, 0}, {255, 255, 255}, {128, 128, 128}, {64, 64, 64},
                    {255, 40, 40}, {255, 140, 0}, {255, 220, 0}, {120, 255, 60},
                    {0, 170, 60}, {0, 220, 255}, {40, 90, 255}, {150, 60, 255},
                    {255, 80, 200}, {255, 190, 150}, {140, 80, 30}, {70, 40, 20}};
        }
        ccColor3B tint = m_layer == 0 ? m_col1 : m_layer == 1 ? m_col2 : ccColor3B{255, 255, 255};
        int i = 0;
        for (auto const& c : cols) {
            auto spr = CCSprite::createWithSpriteFrameName("GJ_colorBtn_001.png");
            if (!spr) spr = CCSprite::create("square02b_001.png");
            if (!spr) continue;
            spr->setScale(0.55f);
            spr->setColor({(GLubyte)(c.r * tint.r / 255), (GLubyte)(c.g * tint.g / 255), (GLubyte)(c.b * tint.b / 255)});
            auto btn = CCMenuItemSpriteExtra::create(spr, this, menu_selector(IconPainterLayer::onColor));
            btn->setTag(i);
            btn->setPosition({(float)(i % 4) * 24.f, -(float)(i / 4) * 24.f});
            m_palette->addChild(btn);
            i++;
        }
        m_paletteColors = cols;
        m_color = cols[0];
    }
    std::vector<ccColor3B> m_paletteColors;

    // ---------- buttons ----------
    void onColor(CCObject* s) {
        int i = static_cast<CCNode*>(s)->getTag();
        if (i >= 0 && i < (int)m_paletteColors.size()) m_color = m_paletteColors[i];
        if (m_tool == 1) m_tool = 0;
        updateStatus();
    }
    void onLayer(CCObject* s) { m_layer = static_cast<CCNode*>(s)->getTag(); rebuildPalette(); updateStatus(); }
    void onTool(CCObject* s) { m_tool = static_cast<CCNode*>(s)->getTag(); updateStatus(); }
    void onBrush(CCObject*) { m_brush = m_brush % 3 + 1; updateStatus(); }
    void onUndo(CCObject*) {
        if (m_undo.empty()) return;
        m_icon = m_undo.back(); m_undo.pop_back();
        redraw();
    }
    void onClear(CCObject*) { pushUndo(); m_icon[m_layer].fill(ip::Px{}); redraw(); }
    void onUse(CCObject*) { m_enabled = !m_enabled; m_dirty = true; updateStatus(); }

    // The file dialog and the decoding run on their own thread, so the game keeps running and the
    // button click can never be delivered twice (that is what kept re-opening the dialog before).
    void onImport(CCObject*) {
#ifdef GEODE_IS_WINDOWS
        if (s_importBusy.exchange(true)) return;
        this->retain();
        std::thread([this] {
            std::vector<uint8_t> rgba; int w = 0, h = 0; std::string err; bool cancelled = false;
            wchar_t path[1024] = L"";
            OPENFILENAMEW ofn{};
            ofn.lStructSize = sizeof(ofn);
            ofn.lpstrFilter = L"Images (*.jpg;*.jpeg;*.png;*.bmp;*.webp;*.gif)\0*.jpg;*.jpeg;*.png;*.bmp;*.webp;*.gif\0All files\0*.*\0";
            ofn.lpstrFile = path;
            ofn.nMaxFile = 1024;
            ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR | OFN_EXPLORER;
            if (!GetOpenFileNameW(&ofn)) cancelled = true;
            else if (!decodeImage(path, rgba, w, h)) err = "That image could not be read.\nTry saving it again as PNG or JPG.";
            queueInMainThread([this, rgba = std::move(rgba), w, h, err, cancelled] {
                s_importBusy = false;
                if (!cancelled) {
                    if (!err.empty()) showMsg(err.c_str());
                    else finishImport(rgba, w, h);
                }
                this->release();
            });
        }).detach();
#else
        showMsg("Image import is Windows only.");
#endif
    }
    static inline std::atomic<bool> s_importBusy{false};

#ifdef GEODE_IS_WINDOWS
    // stb_image first (JPG / PNG / BMP); if that fails, Windows' own decoder (also WebP, GIF, CMYK JPG, ...)
    static bool decodeImage(const wchar_t* path, std::vector<uint8_t>& out, int& w, int& h) {
        FILE* f = nullptr;
        _wfopen_s(&f, path, L"rb");
        if (f) {
            std::vector<uint8_t> bytes;
            uint8_t chunk[65536]; size_t n;
            while ((n = fread(chunk, 1, sizeof chunk, f)) > 0) bytes.insert(bytes.end(), chunk, chunk + n);
            fclose(f);
            int comp = 0;
            uint8_t* img = bytes.empty() ? nullptr : stbi_load_from_memory(bytes.data(), (int)bytes.size(), &w, &h, &comp, 4);
            if (img && w > 0 && h > 0) {
                out.assign(img, img + (size_t)w * h * 4);
                stbi_image_free(img);
                return true;
            }
            if (img) stbi_image_free(img);
        }

        bool ok = false;
        HRESULT co = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        IWICImagingFactory* fac = nullptr; IWICBitmapDecoder* dec = nullptr;
        IWICBitmapFrameDecode* frame = nullptr; IWICFormatConverter* conv = nullptr;
        if (SUCCEEDED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&fac))) &&
            SUCCEEDED(fac->CreateDecoderFromFilename(path, nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &dec)) &&
            SUCCEEDED(dec->GetFrame(0, &frame)) &&
            SUCCEEDED(fac->CreateFormatConverter(&conv)) &&
            SUCCEEDED(conv->Initialize(frame, GUID_WICPixelFormat32bppRGBA, WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom))) {
            UINT uw = 0, uh = 0;
            if (SUCCEEDED(conv->GetSize(&uw, &uh)) && uw > 0 && uh > 0 && uw <= 16384 && uh <= 16384) {
                out.resize((size_t)uw * uh * 4);
                if (SUCCEEDED(conv->CopyPixels(nullptr, uw * 4, (UINT)out.size(), out.data()))) { w = (int)uw; h = (int)uh; ok = true; }
            }
        }
        if (conv) conv->Release();
        if (frame) frame->Release();
        if (dec) dec->Release();
        if (fac) fac->Release();
        if (SUCCEEDED(co)) CoUninitialize();
        return ok;
    }
#endif

    // centre-crop to a square, average down to N x N, put it in L3
    void finishImport(std::vector<uint8_t> const& img, int w, int h) {
        using namespace ip;
        if (w < 1 || h < 1 || img.size() < (size_t)w * h * 4) { showMsg("That image could not be read."); return; }
        int side = (std::min)(w, h), ox = (w - side) / 2, oy = (h - side) / 2;
        pushUndo();
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++) {
            int x0 = ox + x * side / N, x1 = (std::max)(x0 + 1, ox + (x + 1) * side / N);
            int y0 = oy + y * side / N, y1 = (std::max)(y0 + 1, oy + (y + 1) * side / N);
            double r = 0, g = 0, b = 0, a = 0; int cnt = 0;
            for (int yy = y0; yy < y1 && yy < h; yy++) for (int xx = x0; xx < x1 && xx < w; xx++) {
                const uint8_t* p = &img[((size_t)yy * w + xx) * 4];
                double pa = p[3] / 255.0;
                r += p[0] * pa; g += p[1] * pa; b += p[2] * pa; a += p[3]; cnt++;
            }
            Px out{};
            if (cnt && a > 0) {
                double asum = a / 255.0;
                out = {(uint8_t)(r / asum), (uint8_t)(g / asum), (uint8_t)(b / asum), (uint8_t)((a / cnt) > 127 ? 255 : 0)};
            }
            m_icon[2][y * N + x] = out;
        }
        m_layer = 2; rebuildPalette(); updateStatus(); redraw();
    }

    void showMsg(const char* text) { FLAlertLayer::create("Icon Painter", text, "OK")->show(); }

    void leave() {
        CCDirector::sharedDirector()->replaceScene(CCTransitionFade::create(0.4f, GJGarageLayer::scene()));
    }
    void onSave(CCObject*) {
        ip::g_icon = m_icon;
        ip::g_enabled = m_enabled;
        ip::save();
        ip::buildFrames();
        leave();
    }
    void onBack(CCObject*) { leave(); }      // leave without saving
    void keyBackClicked() override { leave(); }

    CCMenuItemSpriteExtra* textBtn(const char* text, SEL_MenuHandler sel, int tag, const char* bg = "GJ_button_04.png", float scale = 0.6f) {
        auto spr = ButtonSprite::create(text, "bigFont.fnt", bg, 0.7f);
        spr->setScale(scale);
        auto btn = CCMenuItemSpriteExtra::create(spr, this, sel);
        btn->setTag(tag);
        return btn;
    }

    bool init() override {
        if (!CCLayer::init()) return false;
        auto win = CCDirector::sharedDirector()->getWinSize();
        auto gm = GameManager::get();
        m_col1 = gm->colorForIdx(gm->getPlayerColor());
        m_col2 = gm->colorForIdx(gm->getPlayerColor2());
        m_icon = ip::g_icon;
        m_enabled = ip::g_hasIcon ? ip::g_enabled : true;

        auto bg = CCSprite::create("GJ_gradientBG.png");
        if (bg) {
            bg->setAnchorPoint({0, 0});
            bg->setScaleX(win.width / bg->getContentSize().width);
            bg->setScaleY(win.height / bg->getContentSize().height);
            bg->setColor({40, 50, 90});
            this->addChild(bg, -10);
        }

        auto title = CCLabelBMFont::create("Icon Painter", "goldFont.fnt");
        title->setScale(0.7f);
        title->setPosition({win.width / 2, win.height - 16});
        this->addChild(title);

        // canvas
        m_canvasSize = (std::min)(210.f, win.height - 90.f);
        m_canvasPos = CCPoint(win.width / 2, win.height / 2 + 6);
        auto frame = CCLayerColor::create({0, 0, 0, 160}, m_canvasSize + 8, m_canvasSize + 8);
        frame->setPosition({m_canvasPos.x - m_canvasSize / 2 - 4, m_canvasPos.y - m_canvasSize / 2 - 4});
        this->addChild(frame);
        m_canvas = CCSprite::create();
        m_canvas->setPosition(m_canvasPos);
        this->addChild(m_canvas, 1);

        m_status = CCLabelBMFont::create("", "chatFont.fnt");
        m_status->setScale(0.75f);
        m_status->setPosition({win.width / 2, m_canvasPos.y - m_canvasSize / 2 - 14});
        this->addChild(m_status);

        auto menu = CCMenu::create();
        menu->setPosition({0, 0});
        this->addChild(menu, 2);

        // left column: layers + tools
        float lx = m_canvasPos.x - m_canvasSize / 2 - 62, y = win.height - 50;
        auto addLeft = [&](CCMenuItemSpriteExtra* b) { b->setPosition({lx, y}); menu->addChild(b); y -= 27; };
        addLeft(textBtn("L1 COL1", menu_selector(IconPainterLayer::onLayer), 0, "GJ_button_01.png"));
        addLeft(textBtn("L2 COL2", menu_selector(IconPainterLayer::onLayer), 1, "GJ_button_01.png"));
        addLeft(textBtn("L3 FREE", menu_selector(IconPainterLayer::onLayer), 2, "GJ_button_01.png"));
        y -= 6;
        addLeft(textBtn("PEN", menu_selector(IconPainterLayer::onTool), 0));
        addLeft(textBtn("ERASER", menu_selector(IconPainterLayer::onTool), 1));
        addLeft(textBtn("FILL", menu_selector(IconPainterLayer::onTool), 2));
        addLeft(textBtn("SIZE", menu_selector(IconPainterLayer::onBrush), 0));
        addLeft(textBtn("UNDO", menu_selector(IconPainterLayer::onUndo), 0));
        addLeft(textBtn("CLEAR", menu_selector(IconPainterLayer::onClear), 0, "GJ_button_06.png"));

        // right column: palette, preview, import, use, save
        float rx = m_canvasPos.x + m_canvasSize / 2 + 26;
        m_palette = CCMenu::create();
        m_palette->setPosition({rx, win.height - 50});
        this->addChild(m_palette, 2);

        auto pvLabel = CCLabelBMFont::create("PREVIEW", "chatFont.fnt");
        pvLabel->setScale(0.6f);
        pvLabel->setPosition({rx + 36, win.height - 152});
        this->addChild(pvLabel);
        m_preview = CCSprite::create();
        m_preview->setPosition({rx + 36, win.height - 178});
        this->addChild(m_preview, 1);

        auto imp = textBtn("IMG (JPG/PNG)", menu_selector(IconPainterLayer::onImport), 0, "GJ_button_02.png", 0.5f);
        imp->setPosition({rx + 36, win.height - 214});
        menu->addChild(imp);

        m_useLabel = CCLabelBMFont::create("USE: ON", "bigFont.fnt");
        m_useLabel->setScale(0.45f);
        auto use = CCMenuItemSpriteExtra::create(m_useLabel, this, menu_selector(IconPainterLayer::onUse));
        use->setPosition({rx + 36, win.height - 240});
        menu->addChild(use);

        auto saveBtn = textBtn("SAVE", menu_selector(IconPainterLayer::onSave), 0, "GJ_button_01.png", 0.8f);
        saveBtn->setPosition({rx + 36, 30});
        menu->addChild(saveBtn);

        auto backSpr = CCSprite::createWithSpriteFrameName("GJ_arrow_01_001.png");
        if (backSpr) {
            auto back = CCMenuItemSpriteExtra::create(backSpr, this, menu_selector(IconPainterLayer::onBack));
            back->setPosition({25, win.height - 25});
            menu->addChild(back);
        }

        rebuildPalette();
        updateStatus();
        redraw();

        this->setTouchEnabled(true);
        this->setKeypadEnabled(true);
        return true;
    }

public:
    static IconPainterLayer* create() {
        auto ret = new IconPainterLayer();
        if (ret->init()) { ret->autorelease(); return ret; }
        delete ret;
        return nullptr;
    }
    static CCScene* scene() {
        auto sc = CCScene::create();
        sc->addChild(IconPainterLayer::create());
        return sc;
    }
};

// =====================================================================================
// Garage button
// =====================================================================================
class $modify(IPGarage, GJGarageLayer) {
    bool init() {
        if (!GJGarageLayer::init()) return false;
        auto win = CCDirector::sharedDirector()->getWinSize();
        auto spr = ButtonSprite::create("DRAW", "bigFont.fnt", "GJ_button_03.png", 0.7f);
        spr->setScale(0.65f);
        auto btn = CCMenuItemSpriteExtra::create(spr, this, menu_selector(IPGarage::onPainter));
        auto menu = CCMenu::create();
        menu->setPosition({win.width - 42, win.height / 2 + 62});
        menu->addChild(btn);
        menu->setID("painter-menu"_spr);
        this->addChild(menu, 20);
        return true;
    }
    void onPainter(CCObject*) {
        CCDirector::sharedDirector()->replaceScene(CCTransitionFade::create(0.4f, IconPainterLayer::scene()));
    }
};
