/** Keep provider-specific configuration names out of product status/error messages. */
export const aiDisplayText = (text: string) => text.replace(/OPENAI_API_KEY/g, "AI API key").replace(/\bOpenAI\b/gi, "AI");
