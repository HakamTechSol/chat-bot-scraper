using Microsoft.AspNetCore.Mvc;
using WikiScraperMVC.Services;

namespace WikiScraperMVC.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class ChatController : ControllerBase
    {
        private readonly AIAnalyzerService _aiService;
        private readonly ILogger<ChatController> _logger;

        public ChatController(AIAnalyzerService aiService, ILogger<ChatController> logger)
        {
            _aiService = aiService;
            _logger = logger;
        }

        /// <summary>
        /// Ask the Gemini-powered AI Brain a question.
        /// Returns a structured response sourced exclusively from FullWikiData.json.
        /// </summary>
        [HttpGet("ask")]
        public async Task<IActionResult> Ask([FromQuery] string question)
        {
            if (string.IsNullOrWhiteSpace(question))
            {
                return BadRequest(new { error = "Question parameter is required." });
            }

            var result = await _aiService.GetAnswer(question);

            // Console logging for debugging
            _logger.LogInformation(
                "──────────────────────────────────────\n" +
                "  Question  : {Question}\n" +
                "  Language  : {Language}\n" +
                "  Status    : {Status}\n" +
                "  Source    : {SourceLink}\n" +
                "  Timestamp : {DataTimestamp}\n" +
                "  Answer    : {AnswerPreview}...\n" +
                "──────────────────────────────────────",
                question,
                result.Language,
                result.Status,
                result.SourceLink,
                result.DataTimestamp,
                result.Answer?.Length > 120
                    ? result.Answer[..120]
                    : result.Answer
            );

            // Return the strict JSON schema to the caller
            return Ok(new
            {
                answer         = result.Answer,
                source_link    = result.SourceLink,
                data_timestamp = result.DataTimestamp,
                language       = result.Language,
                status         = result.Status
            });
        }

        /// <summary>
        /// POST variant — accepts the question in the request body.
        /// Useful for frontend chat UIs sending JSON payloads.
        /// </summary>
        [HttpPost("ask")]
        public async Task<IActionResult> AskPost([FromBody] ChatRequest request)
        {
            if (string.IsNullOrWhiteSpace(request?.Question))
            {
                return BadRequest(new { error = "Question is required in the request body." });
            }

            // Reuse the GET logic
            var result = await _aiService.GetAnswer(request.Question);

            return Ok(new
            {
                answer         = result.Answer,
                source_link    = result.SourceLink,
                data_timestamp = result.DataTimestamp,
                language       = result.Language,
                status         = result.Status
            });
        }
    }

    /// <summary>
    /// Request body for the POST /api/chat/ask endpoint.
    /// </summary>
    public class ChatRequest
    {
        public string Question { get; set; } = "";
    }
}