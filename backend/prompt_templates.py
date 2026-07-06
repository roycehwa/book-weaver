"""
BookMate Prompt Templates
Provider-neutral prompts for shared book AI adapters.
"""

from typing import Optional


class PromptTemplates:
    """
    Collection of optimized prompt templates for book-related AI tasks
    Designed for book overview and chapter-summary tasks.
    """
    
    @staticmethod
    def book_summary(book_content: str, max_length: int = 500) -> str:
        """
        Generate a prompt for full book summary
        
        Args:
            book_content: The full or partial book content to summarize
            max_length: Approximate maximum length of the summary
            
        Returns:
            Formatted prompt string
        """
        prompt = f"""请对以下书籍内容进行专业总结。

【任务要求】
1. 提取核心主题和主要论点
2. 概括关键章节脉络
3. 识别作者的核心观点和方法论
4. 总结应控制在{max_length}字左右
5. 使用学术性但易懂的语言

【输出格式】
- 书籍概述（2-3句）
- 核心论点（分点列出）
- 关键洞察（2-3点）

【书籍内容】
{book_content}

请提供结构化总结："""
        
        return prompt
    
    @staticmethod
    def chapter_summary(
        chapter_content: str, 
        chapter_title: Optional[str] = None,
        max_length: int = 300
    ) -> str:
        """
        Generate a prompt for single chapter summary
        
        Args:
            chapter_content: The chapter content to summarize
            chapter_title: Optional chapter title for context
            max_length: Approximate maximum length of the summary
            
        Returns:
            Formatted prompt string
        """
        title_context = f"章节标题：{chapter_title}\n" if chapter_title else ""
        
        prompt = f"""请对以下章节内容进行精炼总结。

{title_context}【任务要求】
1. 提取本章的核心议题
2. 概括主要论证过程和关键例子
3. 说明本章与全书主题的关联（如可推断）
4. 总结应控制在{max_length}字左右
5. 保持客观，避免过度解读

【输出格式】
- 本章主题（1句）
- 主要内容（2-3点）
- 关键要点（1-2点）

【章节内容】
{chapter_content}

请提供章节总结："""
        
        return prompt
    
    @staticmethod
    def translation(
        text: str, 
        target_lang: str = "zh",
        source_lang: Optional[str] = None
    ) -> str:
        """
        Generate a prompt for academic-style translation
        
        Args:
            text: The text to translate
            target_lang: Target language code
            source_lang: Source language (auto-detect if None)
            
        Returns:
            Formatted prompt string
        """
        # Language mapping
        lang_names = {
            "zh": "中文",
            "en": "英文",
            "ja": "日文",
            "ko": "韩文",
            "fr": "法文",
            "de": "德文",
            "es": "西班牙文",
            "ru": "俄文",
        }
        
        target_lang_name = lang_names.get(target_lang, target_lang)
        source_hint = ""
        
        if source_lang:
            source_lang_name = lang_names.get(source_lang, source_lang)
            source_hint = f"原文语言：{source_lang_name}\n"
        
        prompt = f"""请将以下文本翻译成{target_lang_name}，采用学术规范风格。

{source_hint}【翻译要求】
1. 准确传达原文含义，不遗漏重要信息
2. 使用学术/专业领域的规范表达
3. 保持原文的语气和风格（正式/客观/严谨）
4. 专业术语需准确翻译，必要时保留原文并加注
5. 译文应通顺自然，符合{target_lang_name}表达习惯
6. 适当调整句式结构以符合目标语言规范

【注意事项】
- 人名、地名保留原文或通用译名
- 引用内容保持原样
- 数字、单位视情况转换或保留

【原文】
{text}

【{target_lang_name}译文】"""
        
        return prompt
    
    @staticmethod
    def key_concepts_extraction(text: str, max_concepts: int = 10) -> str:
        """
        Generate a prompt for extracting key concepts from text
        
        Args:
            text: The text to analyze
            max_concepts: Maximum number of concepts to extract
            
        Returns:
            Formatted prompt string
        """
        prompt = f"""请从以下文本中提取关键概念和术语。

【任务要求】
1. 识别核心概念、专业术语和关键词
2. 每个概念提供简要解释（20-30字）
3. 按重要性排序，最多提取{max_concepts}个
4. 标注概念在原文中的语境含义

【输出格式】
对每个概念，按以下格式输出：
1. [概念名] - [简要解释]

【文本内容】
{text}

请提取关键概念："""
        
        return prompt
    
    @staticmethod
    def reading_comprehension_question(text: str, num_questions: int = 3) -> str:
        """
        Generate a prompt for creating reading comprehension questions
        
        Args:
            text: The text to base questions on
            num_questions: Number of questions to generate
            
        Returns:
            Formatted prompt string
        """
        prompt = f"""请基于以下文本生成阅读理解问题。

【任务要求】
1. 生成{num_questions}个高质量理解问题
2. 问题类型应包括：
   - 事实性问题（考查细节理解）
   - 推理性问题（考查逻辑分析）
   - 评价性问题（考查批判思考）
3. 每个问题提供参考答案要点
4. 问题难度适中，避免过于简单或过于晦涩

【输出格式】
Q1: [问题]
A1: [参考答案要点]

【文本内容】
{text}

请生成阅读理解问题："""
        
        return prompt
    
    @staticmethod
    def quote_extraction(text: str, max_quotes: int = 5) -> str:
        """
        Generate a prompt for extracting notable quotes
        
        Args:
            text: The text to extract quotes from
            max_quotes: Maximum number of quotes to extract
            
        Returns:
            Formatted prompt string
        """
        prompt = f"""请从以下文本中提取精彩引语。

【任务要求】
1. 提取最具洞察力、启发性或文学性的{max_quotes}句引语
2. 优先选择：
   - 核心观点的凝练表达
   - 富有哲理的洞察
   - 优美或有力的修辞
3. 对每句引语提供简要解读（1-2句）
4. 注明引语在原文中的语境

【输出格式】
"[引语原文]"
解读：[简要说明其含义和价值]

【文本内容】
{text}

请提取精彩引语："""
        
        return prompt
    
    @staticmethod
    def reading_guide(book_title: str, book_description: str = "") -> str:
        """
        Generate a prompt for creating a reading guide
        
        Args:
            book_title: The title of the book
            book_description: Brief description or context about the book
            
        Returns:
            Formatted prompt string
        """
        desc_section = f"\n【书籍简介】\n{book_description}\n" if book_description else ""
        
        prompt = f"""请为《{book_title}》生成一份阅读指南。

{desc_section}【阅读指南应包含】
1. 阅读前准备：
   - 建议的背景知识
   - 阅读心态和预期
   
2. 核心议题预览：
   - 本书要解决的核心问题
   - 主要论证线索
   
3. 阅读策略建议：
   - 推荐阅读顺序（如适用）
   - 重点章节提示
   - 笔记建议
   
4. 延伸阅读：
   - 相关主题推荐
   - 配套阅读材料类型

【输出格式】
按上述四个部分结构化输出，使用Markdown格式。

请生成阅读指南："""
        
        return prompt
    
    @staticmethod
    def compare_books(book1_info: dict, book2_info: dict) -> str:
        """
        Generate a prompt for comparing two books
        
        Args:
            book1_info: Dict with 'title' and 'content' keys
            book2_info: Dict with 'title' and 'content' keys
            
        Returns:
            Formatted prompt string
        """
        prompt = f"""请比较以下两本书籍的内容和观点。

【书籍A】《{book1_info['title']}》
{book1_info.get('content', '')}

【书籍B】《{book2_info['title']}》
{book2_info.get('content', '')}

【比较维度】
1. 核心主题与立论
2. 方法论差异
3. 观点异同分析
4. 互补性与冲突点
5. 推荐阅读顺序建议

【输出格式】
按上述维度结构化输出对比分析。

请提供比较分析："""
        
        return prompt


# Pre-built prompts for common use cases
class QuickPrompts:
    """Quick access to common prompts without parameters"""
    
    @staticmethod
    def system_prompt() -> str:
        """System prompt for book analysis tasks"""
        return """你是一位专业的阅读助手和学术分析专家。你的任务是帮助用户深入理解书籍内容，提供准确、客观、有洞察力的分析。

核心能力：
1. 精准总结书籍核心观点和论证结构
2. 提取关键概念和专业术语
3. 提供学术规范的翻译
4. 生成有助于理解的辅助材料

行为准则：
- 保持客观中立，准确反映原文内容
- 使用专业但易懂的语言
- 结构化输出，便于阅读
- 承认不确定性，不编造信息"""


# Example usage
if __name__ == "__main__":
    print("=" * 60)
    print("BookMate Prompt Templates - Examples")
    print("=" * 60)
    
    # Example 1: Book summary
    print("\n【示例1：书籍总结模板】")
    print("-" * 40)
    sample_book = "人工智能是计算机科学的一个分支...（此处省略详细内容）"
    print(PromptTemplates.book_summary(sample_book, max_length=200)[:500] + "...\n")
    
    # Example 2: Chapter summary
    print("\n【示例2：章节总结模板】")
    print("-" * 40)
    sample_chapter = "本章探讨了机器学习的理论基础..."
    print(PromptTemplates.chapter_summary(sample_chapter, "第一章：绪论", 150)[:500] + "...\n")
    
    # Example 3: Translation
    print("\n【示例3：学术翻译模板】")
    print("-" * 40)
    sample_text = "The emergence of artificial intelligence has revolutionized..."
    print(PromptTemplates.translation(sample_text, target_lang="zh")[:500] + "...\n")
    
    print("=" * 60)
    print("Templates loaded successfully")
    print("=" * 60)
