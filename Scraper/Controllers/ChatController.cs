using Microsoft.AspNetCore.Mvc;
using WikiScraperMVC.Services;

[ApiController]
[Route("api/[controller]")]
public class ChatController : ControllerBase
{
    private readonly AIAnalyzerService _aiService;

    public ChatController(AIAnalyzerService aiService)
    {
        _aiService = aiService;
    }

    [HttpGet("ask")]
    public async Task<IActionResult> Ask(string question)
    {
        var result = await _aiService.GetAnswer(question);

        // Terminal/Console mein output dikhane ke liye:
        Console.WriteLine("--------------------------------------------------");
        Console.WriteLine($"Question: {question}");
        Console.WriteLine($"Score: {result.score}");
        Console.WriteLine($"URL: {result.url}");
        Console.WriteLine($"Answer: {result.answer?.Substring(0, Math.Min(result.answer.Length, 100))}..."); // Pehle 100 characters
        Console.WriteLine("--------------------------------------------------");

        return Ok(new
        {
            question = question,
            answer = result.answer,
            url = result.url,
            score = result.score
        });
    }


}