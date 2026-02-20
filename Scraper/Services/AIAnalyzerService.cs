using System.Net.Http.Json;

public class AIAnalyzerService
{
    private readonly HttpClient _httpClient;

    public AIAnalyzerService(HttpClient httpClient)
    {
        _httpClient = httpClient;
    }

    public async Task<AIResponse> GetAnswer(string userQuestion) // 👈 return type badal di
    {
        try
        {
            var response = await _httpClient.GetAsync($"http://127.0.0.1:8000/ask?question={Uri.EscapeDataString(userQuestion)}");

            if (response.IsSuccessStatusCode)
            {
                var result = await response.Content.ReadFromJsonAsync<AIResponse>();
                return result; // 👈 Pura object return karein
            }
            return new AIResponse { answer = "Python API error." };
        }
        catch (Exception)
        {
            return new AIResponse { answer = "Could not reach AI Engine." };
        }
    }
}

    public class AIResponse
{
    public string answer { get; set; }
    public string url { get; set; }  
    public double score { get; set; }
}