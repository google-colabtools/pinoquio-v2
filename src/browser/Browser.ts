import playwright, { BrowserContext } from 'rebrowser-playwright'

import { newInjectedContext } from 'fingerprint-injector'
import { FingerprintGenerator } from 'fingerprint-generator'

import { MicrosoftRewardsBot } from '../index'
import { loadSessionData, saveFingerprintData } from '../util/Load'
import { updateFingerprintUserAgent } from '../util/UserAgent'

import { AccountProxy } from '../interface/Account'

/* Test Stuff
https://abrahamjuliot.github.io/creepjs/
https://botcheck.luminati.io/
https://fv.pro/
https://pixelscan.net/
https://www.browserscan.net/
*/

class Browser {
    private bot: MicrosoftRewardsBot
    private blockedDomains: string[] = []

    constructor(bot: MicrosoftRewardsBot) {
        this.bot = bot
    }

    private async loadBlockedDomains(): Promise<string[]> {
        if (this.blockedDomains.length > 0) {
            return this.blockedDomains
        }

        const blocklistUrl = 'https://raw.githubusercontent.com/google-colabtools/pinoquio-v2/refs/heads/main/domain_blocklist.txt'
        const response = await fetch(blocklistUrl, { signal: AbortSignal.timeout(5000) })
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`)
        }

        const text = await response.text()
        this.blockedDomains = text
            .split('\n')
            .map(line => line.trim())
            .filter(line => line && !line.startsWith('#') && !line.startsWith('!'))
        
        this.bot.log(this.bot.isMobile, 'BROWSER', `Loaded ${this.blockedDomains.length} blocked domains`)
        return this.blockedDomains
    }

    async createBrowser(proxy: AccountProxy, email: string): Promise<BrowserContext> {
        // Optional automatic browser installation (set AUTO_INSTALL_BROWSERS=1)
        if (process.env.AUTO_INSTALL_BROWSERS === '1') {
            try {
                // Dynamically import child_process to avoid overhead otherwise
                const { execSync } = await import('child_process')
                execSync('npx playwright install chromium', { stdio: 'ignore' })
            } catch { /* silent */ }
        }

        let browser: import('rebrowser-playwright').Browser
        // Support both legacy and new config structures (wider scope for later usage)
        const cfgAny = this.bot.config as unknown as Record<string, unknown>
        try {
            // FORCE_HEADLESS env takes precedence (used in Docker with headless shell only)
            const envForceHeadless = process.env.FORCE_HEADLESS === '1'
            const headlessValue = envForceHeadless ? true : ((cfgAny['headless'] as boolean | undefined) ?? (cfgAny['browser'] && (cfgAny['browser'] as Record<string, unknown>)['headless'] as boolean | undefined) ?? false)
            const headless: boolean = Boolean(headlessValue)

            const useEdge = process.env.EDGE_ENABLED === 'true'
            const engineName = useEdge ? 'msedge' : 'chromium'
            this.bot.log(this.bot.isMobile, 'BROWSER', `Launching ${engineName} (headless=${headless})`) // explicit engine log
            
            const baseArgs = [
                '--disable-background-networking',
                '--test-type', // Test mode
                '--disable-quic', // Disable QUIC connection
                '--no-first-run', // Skip first run check
                '--blink-settings=imagesEnabled=false', // Disable image loading
                '--no-sandbox', // Disable sandbox mode
                '--mute-audio', // Disable audio
                '--disable-setuid-sandbox', // Disable setuid sandbox
                '--ignore-certificate-errors', // Ignore all certificate errors
                '--ignore-certificate-errors-spki-list', // Ignore certificate errors for specified SPKI list
                '--ignore-ssl-errors', // Ignore SSL errors
            ]
            
            const edgeSpecificArgs = [
                '--disable-translate', // Disable translation popup
                '--disable-features=TranslateUI', // Disable translation features
                '--disable-sync', // Disable sync features
            ]
            
            browser = await playwright.chromium.launch({
                ...(useEdge && { channel: 'msedge' }), // Uses Edge only if EDGE_ENABLED=true
                headless,
                ...(proxy.url && { proxy: { username: proxy.username, password: proxy.password, server: `${proxy.url}:${proxy.port}` } }),
                args: useEdge ? [...baseArgs, ...edgeSpecificArgs] : baseArgs
            })
        } catch (e: unknown) {
            const msg = (e instanceof Error ? e.message : String(e))
            // Common missing browser executable guidance
            if (/Executable doesn't exist/i.test(msg)) {
                this.bot.log(this.bot.isMobile, 'BROWSER', 'Chromium not installed for Playwright. Run: "npx playwright install chromium" (or set AUTO_INSTALL_BROWSERS=1 to auto attempt).', 'error')
            } else {
                this.bot.log(this.bot.isMobile, 'BROWSER', 'Failed to launch browser: ' + msg, 'error')
            }
            throw e
        }

        // Resolve saveFingerprint from legacy root or new fingerprinting.saveFingerprint
        const fpConfig = (cfgAny['saveFingerprint'] as unknown) || ((cfgAny['fingerprinting'] as Record<string, unknown> | undefined)?.['saveFingerprint'] as unknown)
        const saveFingerprint: { mobile: boolean; desktop: boolean } = (fpConfig as { mobile: boolean; desktop: boolean }) || { mobile: false, desktop: false }

        const sessionData = await loadSessionData(this.bot.config.sessionPath, email, this.bot.isMobile, saveFingerprint)

        const fingerprint = sessionData.fingerprint ? sessionData.fingerprint : await this.generateFingerprint()

        const context = await newInjectedContext(browser as unknown as import('playwright').Browser, { fingerprint: fingerprint })

        // Carregar lista de domínios bloqueados
        const blockedDomains = await this.loadBlockedDomains()

        // Block image loading to save data traffic
        await context.route('**/*', (route) => {
            const resourceType = route.request().resourceType()
            const url = route.request().url()

            // Bloquear domínios da lista
            if (blockedDomains.some(domain => url.includes(domain))) {
                return route.abort()
            }

            // Bloquear imagens
            if (resourceType === 'image' || resourceType === 'media') {
                return route.abort()
            }

            // Bloquear fontes (resourceType font ou extensão conhecida)
            if (
                resourceType === 'font' ||
                url.endsWith('.woff') ||
                url.endsWith('.woff2') ||
                url.endsWith('.ttf') ||
                url.endsWith('.otf')
            ) {
                return route.abort()
            }

            return route.continue()
        })

        // Set timeout to preferred amount (supports legacy globalTimeout or browser.globalTimeout)
        const globalTimeout = (cfgAny['globalTimeout'] as unknown) ?? ((cfgAny['browser'] as Record<string, unknown> | undefined)?.['globalTimeout'] as unknown) ?? 30000
        context.setDefaultTimeout(this.bot.utils.stringToMs(globalTimeout as (number | string)))

        // Normalize viewport and page rendering so content fits typical screens
        try {
            const desktopViewport = { width: 1280, height: 800 }
            const mobileViewport = { width: 390, height: 844 }

            context.on('page', async (page) => {
                try {
                    // Set a reasonable viewport size depending on device type
                    if (this.bot.isMobile) {
                        await page.setViewportSize(mobileViewport)
                    } else {
                        await page.setViewportSize(desktopViewport)
                    }

                    // Inject a tiny CSS to avoid gigantic scaling on some environments
                    await page.addInitScript(() => {
                        try {
                            const style = document.createElement('style')
                            style.id = '__mrs_fit_style'
                            style.textContent = `
                              html, body { overscroll-behavior: contain; }
                              /* Mild downscale to keep content within window on very large DPI */
                              @media (min-width: 1000px) {
                                html { zoom: 0.9 !important; }
                              }
                            `
                            document.documentElement.appendChild(style)
                        } catch { /* ignore */ }
                    })
                } catch { /* ignore */ }
            })
        } catch { /* ignore */ }

        await context.addCookies(sessionData.cookies)

        // Persist fingerprint when feature is configured
        if (fpConfig) {
            await saveFingerprintData(this.bot.config.sessionPath, email, this.bot.isMobile, fingerprint)
        }

        this.bot.log(this.bot.isMobile, 'BROWSER', `Created browser with User-Agent: "${fingerprint.fingerprint.navigator.userAgent}"`)

        return context as BrowserContext
    }

    async generateFingerprint() {
        const fingerPrintData = new FingerprintGenerator().getFingerprint({
            devices: this.bot.isMobile ? ['mobile'] : ['desktop'],
            operatingSystems: this.bot.isMobile ? ['android'] : ['windows'],
            browsers: [{ name: 'edge' }]
        })

        const updatedFingerPrintData = await updateFingerprintUserAgent(fingerPrintData, this.bot.isMobile)

        return updatedFingerPrintData
    }
}

export default Browser