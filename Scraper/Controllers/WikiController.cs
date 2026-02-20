using Microsoft.AspNetCore.Mvc;
using WikiScraperMVC.Services;
using System.Text.Json;
using System.Text.Encodings.Web;

namespace WikiScraperMVC.Controllers
{
    [ApiController]
    [Route("api/wiki")]
    public class WikiController : ControllerBase
    {
        private readonly WikiScraperService _scraper;
        private readonly ILogger<WikiController> _logger;

        public WikiController(WikiScraperService scraper, ILogger<WikiController> logger)
        {
            _scraper = scraper;
            _logger = logger;
        }

        [HttpGet("scrape-stream")]
        public async Task ScrapeWithStream()
        {
            Response.Headers.Add("Content-Type", "text/event-stream");
            Response.Headers.Add("Cache-Control", "no-cache");
            Response.Headers.Add("Connection", "keep-alive");

            // API response ke liye bhi Greek readable settings
            var options = new JsonSerializerOptions
            {
                Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
            };

            await foreach (var update in _scraper.ScrapeWithProgressAsync())
            {
                var json = JsonSerializer.Serialize(update, options);
                await Response.WriteAsync($"data: {json}\n\n");
                await Response.Body.FlushAsync();
            }
        }
    }
}