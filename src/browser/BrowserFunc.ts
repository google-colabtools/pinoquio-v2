import { BrowserContext, Page } from 'rebrowser-playwright'
import { CheerioAPI, load } from 'cheerio'
import { AxiosRequestConfig } from 'axios'

import { MicrosoftRewardsBot } from '../index'
import { saveSessionData } from '../util/Load'

import { Counters, DashboardData, MorePromotion, PromotionalItem } from './../interface/DashboardData'
import { QuizData } from './../interface/QuizData'
import { AppUserData } from '../interface/AppUserData'
import { EarnablePoints } from '../interface/Points'


export default class BrowserFunc {
    private bot: MicrosoftRewardsBot

    constructor(bot: MicrosoftRewardsBot) {
        this.bot = bot
    }


    /**
     * Navigate the provided page to rewards homepage
     * @param {Page} page Playwright page
    */
    async goHome(page: Page) {

        try {
            const dashboardURL = new URL(this.bot.config.baseURL)

            if (page.url() === dashboardURL.href) {
                return
            }

            // Retry logic for initial page.goto with progressive timeouts
            const maxAttempts = 5
            
            for (let attempt = 1; attempt <= maxAttempts; attempt++) {
                try {
                    // Progressive timeout: 30s, 45s, 60s, 75s, 90s
                    const timeout = Math.min(30000 + (attempt - 1) * 15000, 90000)
                    
                    // Only log on first attempt or if it's a retry
                    if (attempt === 1) {
                        this.bot.log(this.bot.isMobile, 'GO-HOME', `Navigating to homepage (${timeout/1000}s timeout)`)
                    }
                    
                    await page.goto(this.bot.config.baseURL, { 
                        waitUntil: 'load', 
                        timeout: timeout 
                    })
                    
                    // Only log success if there were retries
                    if (attempt > 1) {
                        this.bot.log(this.bot.isMobile, 'GO-HOME', `Navigation succeeded after ${attempt} attempts`)
                    }
                    break // Success, exit retry loop
                    
                } catch (error: any) {
                    const errorMessage = (error?.message || 'Unknown error')
                        .split('\n')[0] // Take only first line, ignore Call log
                        .replace(/Call log:.*$/s, '') // Remove Call log section
                        .trim()
                    
                    if (attempt < maxAttempts) {
                        const waitTime = 2000 + (attempt - 1) * 1000 // Progressive delay: 2s, 3s, 4s, 5s
                        this.bot.log(this.bot.isMobile, 'GO-HOME', 
                            `Navigation failed, retry ${attempt}/${maxAttempts} in ${waitTime/1000}s`, 
                            'warn'
                        )
                        await this.bot.utils.wait(waitTime)
                    } else {
                        // Final attempt failed
                        this.bot.log(this.bot.isMobile, 'GO-HOME', 
                            `All ${maxAttempts} navigation attempts failed. Last error: ${errorMessage}`, 
                            'error'
                        )
                        throw error
                    }
                }
            }

            const maxIterations = 5 // Maximum iterations set to 5

            for (let iteration = 1; iteration <= maxIterations; iteration++) {
                await this.bot.utils.wait(3000)
                await this.bot.browser.utils.tryDismissAllMessages(page)

                // Check if account is suspended
                const isSuspended = await page.waitForSelector('#suspendedAccountHeader', { state: 'visible', timeout: 10000 }).then(() => true).catch(() => false)
                if (isSuspended) {
                    this.bot.log(this.bot.isMobile, 'GO-HOME', 'This account is suspended!', 'error')
                    // Save screenshot on suspension
                    const screenshotPath = `${this.bot.config.sessionPath}/suspended_${this.bot.isMobile ? 'mobile' : 'desktop'}_${Date.now()}.png`
                    await page.screenshot({ path: screenshotPath, fullPage: true })
                    this.bot.log(this.bot.isMobile, 'GO-HOME', `Screenshot saved to ${screenshotPath}`)
                    throw new Error('Account has been suspended!')
                }

                try {
                    // If activities are found, exit the loop
                    await page.waitForSelector('#more-activities', { timeout: 1000 })
                    this.bot.log(this.bot.isMobile, 'GO-HOME', 'Visited homepage successfully')
                    break

                } catch (error) {
                    // Continue if element is not found
                }

                // Below runs if the homepage was unable to be visited
                const currentURL = new URL(page.url())

                if (currentURL.hostname !== dashboardURL.hostname) {
                    await this.bot.browser.utils.tryDismissAllMessages(page)

                    await this.bot.utils.wait(2000)
                    
                    // Retry logic for secondary page.goto with shorter timeout since we're in a loop
                    const secondaryMaxAttempts = 3
                    
                    for (let secondaryAttempt = 1; secondaryAttempt <= secondaryMaxAttempts; secondaryAttempt++) {
                        try {
                            // Use shorter timeout for secondary attempts: 30s, 45s, 60s
                            const secondaryTimeout = Math.min(30000 + (secondaryAttempt - 1) * 15000, 60000)
                            
                            // Only log on first secondary attempt
                            if (secondaryAttempt === 1) {
                                this.bot.log(this.bot.isMobile, 'GO-HOME', `Redirecting to homepage`)
                            }
                            
                            await page.goto(this.bot.config.baseURL, { 
                                waitUntil: 'load', 
                                timeout: secondaryTimeout 
                            })
                            
                            // Only log if there were retries
                            if (secondaryAttempt > 1) {
                                this.bot.log(this.bot.isMobile, 'GO-HOME', `Redirection succeeded after ${secondaryAttempt} attempts`)
                            }
                            break // Success, exit retry loop
                            
                        } catch (error: any) {
                            if (secondaryAttempt < secondaryMaxAttempts) {
                                const waitTime = 1000 + (secondaryAttempt - 1) * 500 // Progressive delay: 1s, 1.5s
                                this.bot.log(this.bot.isMobile, 'GO-HOME', 
                                    `Redirection failed, retry ${secondaryAttempt}/${secondaryMaxAttempts} in ${waitTime/1000}s`, 
                                    'warn'
                                )
                                await this.bot.utils.wait(waitTime)
                            } else {
                                // Final attempt failed - log warning but continue with outer loop
                                this.bot.log(this.bot.isMobile, 'GO-HOME', 
                                    `Redirection failed after ${secondaryMaxAttempts} attempts, continuing...`, 
                                    'warn'
                                )
                                // Don't throw here, let the outer loop handle it
                            }
                        }
                    }
                } else {
                    this.bot.log(this.bot.isMobile, 'GO-HOME', 'Visited homepage successfully')
                    break
                }

                await this.bot.utils.wait(5000)
            }

        } catch (error) {
            throw this.bot.log(this.bot.isMobile, 'GO-HOME', 'An error occurred:' + error, 'error')
        }
    }

    /**
     * Fetch user dashboard data
     * @returns {DashboardData} Object of user bing rewards dashboard data
    */
    async getDashboardData(page?: Page): Promise<DashboardData> {
        const target = page ?? this.bot.homePage
        const maxRetries = 5;
        const retryDelay = 10000;
        let lastError: any;

        for (let attempt = 1; attempt <= maxRetries; attempt++) {
            try {
                const dashboardURL = new URL(this.bot.config.baseURL)
                const currentURL = new URL(target.url())

                if (currentURL.hostname !== dashboardURL.hostname) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', 'Provided page did not equal dashboard page, redirecting to dashboard page')
                    await this.goHome(target)
                }

                // Check for and reload bad pages (network errors)
                await this.bot.browser.utils.reloadBadPage(target)

                // Ensure to wait long enough before reload
                await this.bot.utils.wait(5000);

                // Progressive timeout strategy: 30s, 45s, 60s, 75s, 90s
                const reloadTimeout = Math.min(30000 + (attempt - 1) * 15000, 90000);
                
                try {
                    // Try reload with networkidle first
                    await target.reload({ waitUntil: 'networkidle', timeout: reloadTimeout })
                } catch (reloadError: any) {
                    // If networkidle fails, try with 'load' as fallback
                    if (reloadError.message?.includes('Timeout')) {
                        this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `NetworkIdle timeout, using load fallback`, 'warn')
                        await target.reload({ waitUntil: 'load', timeout: Math.min(reloadTimeout, 60000) })
                    } else {
                        throw reloadError
                    }
                }
                
                // Try to wait for #more-activities but continue if it fails
                try {
                    await target.waitForSelector('#more-activities', { timeout: 8000 })
                } catch {
                    // Continue without '#more-activities' - no warning needed
                }
                
                // Extra wait to ensure scripts are fully loaded
                await this.bot.utils.waitRandom(3000, 5000); // 3-5 seconds random wait

                // Multiple attempts to get script content
                let scriptContent = null;
                for (let scriptAttempt = 1; scriptAttempt <= 3; scriptAttempt++) {
                    scriptContent = await target.evaluate(() => {
                        const scripts = Array.from(document.querySelectorAll('script'))
                        const targetScript = scripts.find(script => 
                            script.innerText && (
                                script.innerText.includes('var dashboard') || 
                                script.innerText.includes('dashboard =') ||
                                script.innerText.includes('_w.dashboard')
                            )
                        )
                        return targetScript?.innerText || null
                    })
                    
                    if (scriptContent) {
                        break;
                    }
                    
                    if (scriptAttempt < 3) {
                        this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Script not found, retrying ${scriptAttempt}/3`, 'warn')
                        await this.bot.utils.wait(2000);
                    }
                }

                if (!scriptContent) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Dashboard data not found, retry ${attempt}/${maxRetries}`, 'warn')
                    throw new Error('Dashboard data not found within script')
                }

                const dashboardData = await target.evaluate((scriptContent: string) => {
                    try {
                        // Try multiple possible extraction methods
                        const regexes = [
                            /var dashboard = (\{.*?\});/s,
                            /dashboard = (\{.*?\});/s,
                            /\_w\.dashboard = (\{.*?\});/s
                        ]
                        
                        for (const regex of regexes) {
                            const match = regex.exec(scriptContent)
                            if (match && match[1]) {
                                return JSON.parse(match[1])
                            }
                        }
                        return null
                    } catch (e) {
                        console.error('Failed to parse dashboard data:', e)
                        return null
                    }
                }, scriptContent)

                if (!dashboardData) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Failed to parse dashboard data, retry ${attempt}/${maxRetries}`, 'warn')
                    throw new Error('Unable to parse dashboard script')
                }

                if (attempt > 1) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Successfully fetched dashboard data, attempts: ${attempt}`)
                }

                return dashboardData

            } catch (error: any) {
                lastError = error
                // Clean up Playwright Call log noise
                const errorMessage = (error?.message || 'Unknown error')
                    .split('\n')[0] // Take only first line, ignore Call log
                    .replace(/Call log:.*$/s, '') // Remove Call log section
                    .trim()
                
                // Specific strategies for different error types
                if (errorMessage.includes('net::ERR_TIMED_OUT')) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Network timeout, attempting page refresh`, 'warn')
                    try {
                        await target.reload({ waitUntil: 'load', timeout: 60000 })
                    } catch (reloadError) {
                        this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', 'Page reload failed, will retry from beginning', 'error')
                    }
                } else if (errorMessage.includes('net::ERR_ABORTED')) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Page reload aborted, will retry`, 'warn')
                    // For aborted requests, just wait and retry - no additional action needed
                } else if (errorMessage.includes('Timeout') && errorMessage.includes('reload')) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Reload timeout, will retry with longer timeout`, 'warn')
                    // No extra action, just wait for retry
                } else if (errorMessage.includes('Navigation') || errorMessage.includes('navigation')) {
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Navigation error, redirecting home`, 'warn')
                    try {
                        await this.goHome(target)
                    } catch (homeError) {
                        this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', 'Failed to navigate home, will retry from current state', 'warn')
                    }
                }
                
                if (attempt < maxRetries) {
                    const waitTime = retryDelay + (attempt - 1) * 2000; // Progressive delay: 10s, 12s, 14s, 16s
                    this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', `Retry ${attempt}/${maxRetries} in ${waitTime/1000}s`, 'warn')
                    await this.bot.utils.wait(waitTime)
                    
                    // Try to refresh login status before retry (only on attempt 2)
                    if (attempt === 2) {
                        this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', 'Trying to revalidate login status...')
                        try {
                            await this.goHome(target)
                        } catch (homeError) {
                            this.bot.log(this.bot.isMobile, 'DASHBOARD-DATA', 'Login revalidation failed, continuing with retry...', 'warn')
                        }
                    }
                }
            }
        }

        throw this.bot.log(this.bot.isMobile, 'GET-DASHBOARD-DATA', `Failed after ${maxRetries} attempts. Last error: ${(lastError?.message || lastError || 'Unknown error').split('\n')[0]}`, 'error')
    }

    /**
     * Get search point counters
     * @returns {Counters} Object of search counter data
    */
    async getSearchPoints(): Promise<Counters> {
        const dashboardData = await this.getDashboardData() // Always fetch newest data

        return dashboardData.userStatus.counters
    }

    /**
     * Get total earnable points with web browser
     * @returns {number} Total earnable points
    */
    async getBrowserEarnablePoints(): Promise<EarnablePoints> {
        try {
            let desktopSearchPoints = 0
            let mobileSearchPoints = 0
            let dailySetPoints = 0
            let morePromotionsPoints = 0

            const data = await this.getDashboardData()

            // Desktop Search Points
            if (data.userStatus.counters.pcSearch?.length) {
                data.userStatus.counters.pcSearch.forEach(x => desktopSearchPoints += (x.pointProgressMax - x.pointProgress))
            }

            // Mobile Search Points
            if (data.userStatus.counters.mobileSearch?.length) {
                data.userStatus.counters.mobileSearch.forEach(x => mobileSearchPoints += (x.pointProgressMax - x.pointProgress))
            }

            // Daily Set
            data.dailySetPromotions[this.bot.utils.getFormattedDate()]?.forEach(x => dailySetPoints += (x.pointProgressMax - x.pointProgress))

            // More Promotions
            if (data.morePromotions?.length) {
                data.morePromotions.forEach(x => {
                    // Only count points from supported activities
                    if (['quiz', 'urlreward'].includes(x.promotionType) && x.exclusiveLockedFeatureStatus !== 'locked') {
                        morePromotionsPoints += (x.pointProgressMax - x.pointProgress)
                    }
                })
            }

            const totalEarnablePoints = desktopSearchPoints + mobileSearchPoints + dailySetPoints + morePromotionsPoints

            return {
                dailySetPoints,
                morePromotionsPoints,
                desktopSearchPoints,
                mobileSearchPoints,
                totalEarnablePoints
            }
        } catch (error) {
            throw this.bot.log(this.bot.isMobile, 'GET-BROWSER-EARNABLE-POINTS', 'An error occurred:' + error, 'error')
        }
    }

    /**
     * Get total earnable points with mobile app
     * @returns {number} Total earnable points
    */
    async getAppEarnablePoints(accessToken: string) {
        try {
            const points = {
                readToEarn: 0,
                checkIn: 0,
                totalEarnablePoints: 0
            }

            const eligibleOffers = [
                'ENUS_readarticle3_30points',
                'Gamification_Sapphire_DailyCheckIn'
            ]

            const data = await this.getDashboardData()
            // Guard against missing profile/attributes and undefined settings
            let geoLocale = data?.userProfile?.attributes?.country || 'US'
            const useGeo = !!(this.bot?.config?.searchSettings?.useGeoLocaleQueries)
            geoLocale = (useGeo && typeof geoLocale === 'string' && geoLocale.length === 2)
                ? geoLocale.toLowerCase()
                : 'us'

            const userDataRequest: AxiosRequestConfig = {
                url: 'https://prod.rewardsplatform.microsoft.com/dapi/me?channel=SAAndroid&options=613',
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                    'X-Rewards-Country': geoLocale,
                    'X-Rewards-Language': 'en'
                }
            }

            const userDataResponse: AppUserData = (await this.bot.axios.request(userDataRequest)).data
            const userData = userDataResponse.response
            const eligibleActivities = userData.promotions.filter((x) => eligibleOffers.includes(x.attributes.offerid ?? ''))

            for (const item of eligibleActivities) {
                if (item.attributes.type === 'msnreadearn') {
                    points.readToEarn = parseInt(item.attributes.pointmax ?? '') - parseInt(item.attributes.pointprogress ?? '')
                    break
                } else if (item.attributes.type === 'checkin') {
                    const checkInDay = parseInt(item.attributes.progress ?? '') % 7

                    if (checkInDay < 6 && (new Date()).getDate() != (new Date(item.attributes.last_updated ?? '')).getDate()) {
                        points.checkIn = parseInt(item.attributes['day_' + (checkInDay + 1) + '_points'] ?? '')
                    }
                    break
                }
            }

            points.totalEarnablePoints = points.readToEarn + points.checkIn

            return points
        } catch (error) {
            throw this.bot.log(this.bot.isMobile, 'GET-APP-EARNABLE-POINTS', 'An error occurred:' + error, 'error')
        }
    }

    /**
     * Get current point amount
     * @returns {number} Current total point amount
    */
    async getCurrentPoints(): Promise<number> {
        try {
            const data = await this.getDashboardData()

            return data.userStatus.availablePoints
        } catch (error) {
            throw this.bot.log(this.bot.isMobile, 'GET-CURRENT-POINTS', 'An error occurred:' + error, 'error')
        }
    }

    /**
     * Parse quiz data from provided page
     * @param {Page} page Playwright page
     * @returns {QuizData} Quiz data object
    */
    async getQuizData(page: Page): Promise<QuizData> {
        try {
            const html = await page.content()
            const $ = load(html)

            const scriptContent = $('script')
                .toArray()
                .map((el: any) => $(el).text())
                .find((t: string) => t.includes('_w.rewardsQuizRenderInfo')) || ''

            if (scriptContent) {
                const regex = /_w\.rewardsQuizRenderInfo\s*=\s*({.*?});/s
                const match = regex.exec(scriptContent)

                if (match && match[1]) {
                    const quizData = JSON.parse(match[1])
                    return quizData
                } else {
                    throw this.bot.log(this.bot.isMobile, 'GET-QUIZ-DATA', 'Quiz data not found within script', 'error')
                }
            } else {
                throw this.bot.log(this.bot.isMobile, 'GET-QUIZ-DATA', 'Script containing quiz data not found', 'error')
            }

        } catch (error) {
            throw this.bot.log(this.bot.isMobile, 'GET-QUIZ-DATA', 'An error occurred:' + error, 'error')
        }

    }

    async waitForQuizRefresh(page: Page): Promise<boolean> {
        try {
            await page.waitForSelector('span.rqMCredits', { state: 'visible', timeout: 10000 })
            await this.bot.utils.wait(2000)

            return true
        } catch (error) {
            this.bot.log(this.bot.isMobile, 'QUIZ-REFRESH', 'An error occurred:' + error, 'error')
            return false
        }
    }

    async checkQuizCompleted(page: Page): Promise<boolean> {
        try {
            await page.waitForSelector('#quizCompleteContainer', { state: 'visible', timeout: 2000 })
            await this.bot.utils.wait(2000)

            return true
        } catch (error) {
            return false
        }
    }

    async loadInCheerio(page: Page): Promise<CheerioAPI> {
        const html = await page.content()
        const $ = load(html)

        return $
    }

    async getPunchCardActivity(page: Page, activity: PromotionalItem | MorePromotion): Promise<string> {
        let selector = ''
        try {
            const html = await page.content()
            const $ = load(html)

                const element = $('.offer-cta').toArray().find((x: unknown) => {
                    const el = x as { attribs?: { href?: string } }
                    return !!el.attribs?.href?.includes(activity.offerId)
                })
            if (element) {
                selector = `a[href*="${element.attribs.href}"]`
            }
        } catch (error) {
            this.bot.log(this.bot.isMobile, 'GET-PUNCHCARD-ACTIVITY', 'An error occurred:' + error, 'error')
        }

        return selector
    }

    async closeBrowser(browser: BrowserContext, email: string) {
        try {
            // Save cookies
            await saveSessionData(this.bot.config.sessionPath, browser, email, this.bot.isMobile)

            await this.bot.utils.wait(2000)

            // Close browser
            await browser.close()
            this.bot.log(this.bot.isMobile, 'CLOSE-BROWSER', 'Browser closed cleanly!')
        } catch (error) {
            throw this.bot.log(this.bot.isMobile, 'CLOSE-BROWSER', 'An error occurred:' + error, 'error')
        }
    }
}