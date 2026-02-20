using WikiScraperMVC.Services;
using Hangfire;
using Hangfire.MemoryStorage;

var builder = WebApplication.CreateBuilder(args);

// --- Standard API Services ---
builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

// --- 1. Hangfire Configuration ---
builder.Services.AddHangfire(config => config
    .SetDataCompatibilityLevel(CompatibilityLevel.Version_180)
    .UseSimpleAssemblyNameTypeSerializer()
    .UseRecommendedSerializerSettings()
    .UseMemoryStorage());

builder.Services.AddHangfireServer();

// --- 2. Service Registration (Dependency Injection) ---

// Standard Scraper Service
builder.Services.AddScoped<WikiScraperService>();

// ✅ FIXED LINE: AIAnalyzerService ko HttpClient ke saath register kiya
// Is se aapka "Unable to resolve service for HttpClient" wala error khatam ho jayega
builder.Services.AddHttpClient<AIAnalyzerService>();

var app = builder.Build();

// --- Pipeline Configuration ---
if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseHttpsRedirection();
app.UseStaticFiles();
app.UseRouting();

// --- 3. Hangfire Dashboard ---
app.UseHangfireDashboard();

// --- 4. Automation Schedule (Weekly Monday) ---
using (var scope = app.Services.CreateScope())
{
    RecurringJob.AddOrUpdate<WikiScraperService>(
        "weekly-wiki-scrape",
        service => service.StartScraping(),
        Cron.Weekly(DayOfWeek.Monday)
    );
}

app.MapControllers();
app.Run();