
//using PuppeteerSharp;
//using System.Text.Json;
//using System.Text.Encodings.Web;
//using WikiScraperMVC.Models;
//using System.IO;
//using Microsoft.Extensions.Logging;
//using System.Threading.Channels;

//namespace WikiScraperMVC.Services
//{
//    public class ScrapeResult
//    {
//        public string title { get; set; } = "";
//        public string content { get; set; } = "";
//        public List<string> links { get; set; } = new();
//    }

//    public class ScrapingProgressUpdate
//    {
//        public int PageNumber { get; set; }
//        public string Url { get; set; } = "";
//        public string Title { get; set; } = "";
//        public string Status { get; set; } = "";
//        public int TotalScraped { get; set; }
//        public int QueueCount { get; set; }
//        public string? ErrorMessage { get; set; }
//    }

//    public class WikiScraperService
//    {
//        private readonly ILogger<WikiScraperService> _logger;
//        private readonly string _filePath = Path.Combine(Directory.GetCurrentDirectory(), "FullWikiData.json");
//        private readonly JsonSerializerOptions _jsonOptions = new()
//        {
//            WriteIndented = true,
//            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
//        };

//        public WikiScraperService(ILogger<WikiScraperService> logger)
//        {
//            _logger = logger;
//        }

//        public async IAsyncEnumerable<ScrapingProgressUpdate> ScrapeWithProgressAsync()
//        {
//            var channel = Channel.CreateUnbounded<ScrapingProgressUpdate>();
//            _ = Task.Run(async () =>
//            {
//                try { await PerformScrapingAsync(channel.Writer); }
//                catch (Exception ex)
//                {
//                    _logger.LogError(ex, "Fatal error in scraping task");
//                    await channel.Writer.WriteAsync(new ScrapingProgressUpdate { Status = "error", ErrorMessage = ex.Message });
//                }
//                finally { channel.Writer.Complete(); }
//            });

//            await foreach (var update in channel.Reader.ReadAllAsync()) yield return update;
//        }

//        private async Task PerformScrapingAsync(ChannelWriter<ScrapingProgressUpdate> writer)
//        {
//            var baseUrl = "https://wiki.soft1.eu";
//            var urlsToVisit = new Queue<string>();
//            urlsToVisit.Enqueue(baseUrl);

//            // 1. Purana data load karein
//            List<WikiPage> allPages = await LoadExistingDataAsync();

//            // 2. Visited set ko file ke data se fill karein taake dobara visit na ho
//            var visitedUrlsInSession = new HashSet<string>(allPages.Select(p => p.Url.TrimEnd('/')));

//            _logger.LogInformation("🚀 Resume Logic: {Count} pages already in file. Finding new ones...", allPages.Count);

//            try
//            {
//                string? wsEndpoint;
//                using (var httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(10) })
//                {
//                    var response = await httpClient.GetStringAsync("http://127.0.0.1:9222/json/version");
//                    wsEndpoint = JsonDocument.Parse(response).RootElement.GetProperty("webSocketDebuggerUrl").GetString();
//                }

//                using var browser = await Puppeteer.ConnectAsync(new ConnectOptions { BrowserWSEndpoint = wsEndpoint });
//                using var page = await browser.NewPageAsync();
//                await page.SetViewportAsync(new ViewPortOptions { Width = 1920, Height = 1080 });

//                while (urlsToVisit.Count > 0)
//                {
//                    var currentUrl = urlsToVisit.Dequeue().Split('#')[0].TrimEnd('/');

//                    // --- SMART SKIP LOGIC ---
//                    // Agar URL file mein hai, toh usey visit nahi karenge, bas aage barh jayenge
//                    // Taake aapka 2000 pages ka time bache.
//                    var existingPage = allPages.FirstOrDefault(p => p.Url.TrimEnd('/') == currentUrl);
//                    bool alreadyExists = existingPage != null;

//                    await writer.WriteAsync(new ScrapingProgressUpdate
//                    {
//                        Url = currentUrl,
//                        Status = alreadyExists ? "already_exists_skipping" : "scraping",
//                        TotalScraped = allPages.Count,
//                        QueueCount = urlsToVisit.Count
//                    });

//                    // Agar naya page hai toh scrape karein, warna bas links extract karne ke liye visit karein (Optional)
//                    // Filhaal hum fast kaam ke liye sirf naye pages scrape kar rahe hain.
//                    if (alreadyExists)
//                    {
//                        // Hum yahan naye links find karne ke liye visit kar sakte hain, 
//                        // lekin agar aap sirf "Baqi 2000 ke baad" wala chah rahay hain toh direct continue:
//                        // continue; 
//                    }

//                    try
//                    {
//                        await page.GoToAsync(currentUrl, new NavigationOptions { WaitUntil = new[] { WaitUntilNavigation.Networkidle2 }, Timeout = 60000 });
//                        await Task.Delay(1000); // 1 sec delay for safety

//                        var result = await page.EvaluateFunctionAsync<ScrapeResult>(@"() => {
//                            const allLinks = Array.from(document.querySelectorAll('a'))
//                                .map(a => a.href)
//                                .filter(href => href && (href.includes('wiki.soft1.eu/space/') || href.includes('wiki.soft1.eu/display/')));
//                            const main = document.querySelector('#main-content') || document.querySelector('.wiki-content') || document.body;
//                            const clone = main.cloneNode(true);
//                            ['script', 'style', 'nav', 'footer', 'header', 'aside'].forEach(t => clone.querySelectorAll(t).forEach(el => el.remove()));
//                            return {
//                                title: document.title,
//                                content: clone.innerText.replace(/\s+/g, ' ').trim(),
//                                links: Array.from(new Set(allLinks))
//                            };
//                        }");

//                        if (!string.IsNullOrEmpty(result.title))
//                        {
//                            var newPageData = new WikiPage { Title = result.title, Url = currentUrl, Content = result.content, ScrapedAt = DateTime.Now };

//                            if (alreadyExists)
//                            {
//                                // Monday Sync Logic: Agar content change hai toh update karo
//                                if (existingPage!.Content != newPageData.Content)
//                                {
//                                    int idx = allPages.IndexOf(existingPage);
//                                    allPages[idx] = newPageData;
//                                    await SaveAllDataAsync(allPages);
//                                    _logger.LogInformation("🔄 Updated content for: {Url}", currentUrl);
//                                }
//                            }
//                            else
//                            {
//                                // Bilkul naya page mila
//                                allPages.Add(newPageData);
//                                await SaveAllDataAsync(allPages);
//                                _logger.LogInformation("🆕 Added new page: {Url}", currentUrl);
//                            }

//                            // Naye links queue mein daalein
//                            foreach (var link in result.links)
//                            {
//                                var cleanL = link.Split('?')[0].Split('#')[0].TrimEnd('/');
//                                if (!visitedUrlsInSession.Contains(cleanL))
//                                {
//                                    visitedUrlsInSession.Add(cleanL);
//                                    urlsToVisit.Enqueue(cleanL);
//                                }
//                            }
//                        }
//                    }
//                    catch (Exception ex) { _logger.LogWarning("⚠️ Skipping {Url}: {Msg}", currentUrl, ex.Message); }
//                }
//                await writer.WriteAsync(new ScrapingProgressUpdate { Status = "done", TotalScraped = allPages.Count });
//            }
//            catch (Exception ex) { _logger.LogError("❌ FATAL: {Message}", ex.Message); }
//        }

//        private async Task<List<WikiPage>> LoadExistingDataAsync()
//        {
//            if (!File.Exists(_filePath)) return new List<WikiPage>();
//            try
//            {
//                var json = await File.ReadAllTextAsync(_filePath);
//                return JsonSerializer.Deserialize<List<WikiPage>>(json) ?? new List<WikiPage>();
//            }
//            catch { return new List<WikiPage>(); }
//        }

//        private async Task SaveAllDataAsync(List<WikiPage> data)
//        {
//            try
//            {
//                var json = JsonSerializer.Serialize(data, _jsonOptions);
//                await File.WriteAllTextAsync(_filePath, json);
//            }
//            catch (Exception ex) { _logger.LogError("File Save Error: {Msg}", ex.Message); }
//        }

//        public async Task StartScraping()
//        {
//            var dummyChannel = Channel.CreateUnbounded<ScrapingProgressUpdate>();
//            await PerformScrapingAsync(dummyChannel.Writer);
//        }
//    }
//}


using PuppeteerSharp;
using System.Text.Json;
using System.Text.Encodings.Web;
using WikiScraperMVC.Models;
using System.IO;
using Microsoft.Extensions.Logging;
using System.Threading.Channels;

namespace WikiScraperMVC.Services
{
    public class ScrapeResult
    {
        public string title { get; set; } = "";
        public string content { get; set; } = "";
        public List<string> links { get; set; } = new();
    }

    public class ScrapingProgressUpdate
    {
        public int PageNumber { get; set; }
        public string Url { get; set; } = "";
        public string Title { get; set; } = "";
        public string Status { get; set; } = "";
        public int TotalScraped { get; set; }
        public int QueueCount { get; set; }
        public string? ErrorMessage { get; set; }
    }

    public class WikiScraperService
    {
        private readonly ILogger<WikiScraperService> _logger;
        private readonly string _filePath = Path.Combine(Directory.GetCurrentDirectory(), "FullWikiData.json");
        private readonly JsonSerializerOptions _jsonOptions = new()
        {
            WriteIndented = true,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
        };

        public WikiScraperService(ILogger<WikiScraperService> logger)
        {
            _logger = logger;
        }

        public async IAsyncEnumerable<ScrapingProgressUpdate> ScrapeWithProgressAsync()
        {
            var channel = Channel.CreateUnbounded<ScrapingProgressUpdate>();
            _ = Task.Run(async () =>
            {
                try { await PerformScrapingAsync(channel.Writer); }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Fatal error in scraping task");
                    await channel.Writer.WriteAsync(new ScrapingProgressUpdate { Status = "error", ErrorMessage = ex.Message });
                }
                finally { channel.Writer.Complete(); }
            });

            await foreach (var update in channel.Reader.ReadAllAsync()) yield return update;
        }

        private async Task PerformScrapingAsync(ChannelWriter<ScrapingProgressUpdate> writer)
        {
            var baseUrl = "https://wiki.soft1.eu";
            var urlsToVisit = new Queue<string>();
            urlsToVisit.Enqueue(baseUrl);

            List<WikiPage> allPages = await LoadExistingDataAsync();
            var visitedUrlsInSession = new HashSet<string>(allPages.Select(p => p.Url.TrimEnd('/')));

            _logger.LogInformation("🚀 Live Server Mode: {Count} existing pages found.", allPages.Count);

            try
            {
                // 1. Browser download logic (Server par Chrome hona lazmi hai)
                var browserFetcher = new BrowserFetcher();
                await browserFetcher.DownloadAsync();

                // 2. Launch Browser in Headless Mode (No Window)
                using var browser = await Puppeteer.LaunchAsync(new LaunchOptions
                {
                    Headless = true, // Window nahi khulegi background mein chalega
                    Args = new[] {
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage" // Linux server memory fix
                    }
                });

                using var page = await browser.NewPageAsync();
                await page.SetViewportAsync(new ViewPortOptions { Width = 1920, Height = 1080 });

                while (urlsToVisit.Count > 0)
                {
                    var currentUrl = urlsToVisit.Dequeue().Split('#')[0].TrimEnd('/');
                    var existingPage = allPages.FirstOrDefault(p => p.Url.TrimEnd('/') == currentUrl);
                    bool alreadyExists = existingPage != null;

                    await writer.WriteAsync(new ScrapingProgressUpdate
                    {
                        Url = currentUrl,
                        Status = alreadyExists ? "already_exists_skipping" : "scraping",
                        TotalScraped = allPages.Count,
                        QueueCount = urlsToVisit.Count
                    });

                    // Server performance ke liye: Agar page pehle se hai to scrape na karo (Skip)
                    // Agar Monday update karna hai to ye skip hata sakte hain
                    if (alreadyExists) continue;

                    try
                    {
                        await page.GoToAsync(currentUrl, new NavigationOptions { WaitUntil = new[] { WaitUntilNavigation.Networkidle2 }, Timeout = 60000 });
                        await Task.Delay(1000); // Server throtelling se bachne ke liye

                        var result = await page.EvaluateFunctionAsync<ScrapeResult>(@"() => {
                            const allLinks = Array.from(document.querySelectorAll('a'))
                                .map(a => a.href)
                                .filter(href => href && (href.includes('wiki.soft1.eu/space/') || href.includes('wiki.soft1.eu/display/')));
                            const main = document.querySelector('#main-content') || document.querySelector('.wiki-content') || document.body;
                            const clone = main.cloneNode(true);
                            ['script', 'style', 'nav', 'footer', 'header', 'aside'].forEach(t => clone.querySelectorAll(t).forEach(el => el.remove()));
                            return {
                                title: document.title,
                                content: clone.innerText.replace(/\s+/g, ' ').trim(),
                                links: Array.from(new Set(allLinks))
                            };
                        }");

                        if (!string.IsNullOrEmpty(result.title))
                        {
                            var newPageData = new WikiPage { Title = result.title, Url = currentUrl, Content = result.content, ScrapedAt = DateTime.Now };

                            allPages.Add(newPageData);
                            await SaveAllDataAsync(allPages); // Har page ke baad save (Safe mode)

                            foreach (var link in result.links)
                            {
                                var cleanL = link.Split('?')[0].Split('#')[0].TrimEnd('/');
                                if (!visitedUrlsInSession.Contains(cleanL))
                                {
                                    visitedUrlsInSession.Add(cleanL);
                                    urlsToVisit.Enqueue(cleanL);
                                }
                            }
                        }
                    }
                    catch (Exception ex) { _logger.LogWarning("⚠️ Error at {Url}: {Msg}", currentUrl, ex.Message); }
                }
                await writer.WriteAsync(new ScrapingProgressUpdate { Status = "done", TotalScraped = allPages.Count });
            }
            catch (Exception ex) { _logger.LogError("❌ FATAL: {Message}", ex.Message); }
        }

        private async Task<List<WikiPage>> LoadExistingDataAsync()
        {
            if (!File.Exists(_filePath)) return new List<WikiPage>();
            try
            {
                var json = await File.ReadAllTextAsync(_filePath);
                return JsonSerializer.Deserialize<List<WikiPage>>(json) ?? new List<WikiPage>();
            }
            catch { return new List<WikiPage>(); }
        }

        private async Task SaveAllDataAsync(List<WikiPage> data)
        {
            try
            {
                var json = JsonSerializer.Serialize(data, _jsonOptions);
                await File.WriteAllTextAsync(_filePath, json);
            }
            catch (Exception ex) { _logger.LogError("Save Error: {Msg}", ex.Message); }
        }

        public async Task StartScraping()
        {
            var dummyChannel = Channel.CreateUnbounded<ScrapingProgressUpdate>();
            await PerformScrapingAsync(dummyChannel.Writer);
        }
    }
}