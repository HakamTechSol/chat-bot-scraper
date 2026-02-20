using System.ComponentModel.DataAnnotations;

namespace WikiScraperMVC.Models
{
    public class WikiPage
    {
        [Key]
        public int Id { get; set; }

        [Required]
        public string Title { get; set; } = "";

        [Required]
        [Url]
        public string Url { get; set; } = "";

        public string Content { get; set; } = "";

        // ✅ YEH LINE ADD KARO AGAR BODY HTML CHAHIYE
        public string BodyHtml { get; set; } = "";

        public DateTime ScrapedAt { get; set; }

        // Optional: Add these if needed
        public DateTime CreatedAt { get; set; } = DateTime.Now;
        public DateTime? UpdatedAt { get; set; }
    }
}