using System.Net.Http.Json;
using System.Text.Json;

namespace WikiScraperMVC.Services
{
    public class AIAnalyzerService
    {
        private readonly HttpClient _httpClient;
        private readonly ILogger<AIAnalyzerService> _logger;

        public AIAnalyzerService(HttpClient httpClient, ILogger<AIAnalyzerService> logger)
        {
            _httpClient = httpClient;
            _logger = logger;
        }

        /// <summary>
        /// Sends a question to the Gemini-powered Python AI Engine
        /// and returns the structured response.
        /// </summary>
        public async Task<GeminiResponse> GetAnswer(string userQuestion)
        {
            try
            {
                var encodedQuestion = Uri.EscapeDataString(userQuestion);
                var response = await _httpClient.GetAsync(
                    $"http://127.0.0.1:8000/ask?question={encodedQuestion}"
                );

                if (response.IsSuccessStatusCode)
                {
                    // The AI Brain returns raw JSON as plain text
                    var rawJson = await response.Content.ReadAsStringAsync();

                    var result = JsonSerializer.Deserialize<GeminiResponse>(
                        rawJson,
                        new JsonSerializerOptions { PropertyNameCaseInsensitive = true }
                    );

                    return result ?? new GeminiResponse
                    {
                        Answer = "Empty response from AI Engine.",
                        Status = "error"
                    };
                }

                _logger.LogWarning("AI Engine returned status {StatusCode}", response.StatusCode);
                return new GeminiResponse
                {
                    Answer = $"AI Engine returned HTTP {(int)response.StatusCode}.",
                    Status = "error"
                };
            }
            catch (HttpRequestException ex)
            {
                _logger.LogError(ex, "Could not reach AI Engine at http://127.0.0.1:8000");
                return new GeminiResponse
                {
                    Answer = "Could not reach AI Engine. Is the Python service running?",
                    Status = "error"
                };
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Unexpected error calling AI Engine");
                return new GeminiResponse
                {
                    Answer = "An unexpected error occurred.",
                    Status = "error"
                };
            }
        }
    }

    /// <summary>
    /// Matches the strict JSON schema returned by the Gemini AI Brain.
    /// </summary>
    public class GeminiResponse
    {
        [System.Text.Json.Serialization.JsonPropertyName("answer")]
        public string Answer { get; set; } = "";

        [System.Text.Json.Serialization.JsonPropertyName("source_link")]
        public string SourceLink { get; set; } = "";

        [System.Text.Json.Serialization.JsonPropertyName("data_timestamp")]
        public string DataTimestamp { get; set; } = "";

        [System.Text.Json.Serialization.JsonPropertyName("language")]
        public string Language { get; set; } = "English";

        [System.Text.Json.Serialization.JsonPropertyName("status")]
        public string Status { get; set; } = "not_found";
    }
}
