from src.states.blogstate import BlogState
from langchain_core.messages import SystemMessage, HumanMessage
from src.states.blogstate import Blog

class BlogNode:
    """
    A class to represent blog nodes
    """

    def __init__(self,llm):
        self.llm = llm
    
    def title_creation(self,state:BlogState):
        """
        Create title for the blog
        """

        if "topic" in state and state["topic"]:
            prompt="""
                You are an expert blog content writer. Use Markdown formatting. Generate a blog title for the {topic}. Only give a single Title name in a concise and creative way.
                """
            
            system_message=prompt.format(topic=state["topic"])
            response = self.llm.invoke(system_message)
            return {"blog":{"title":response.content}}
        
    def content_generation(self,state:BlogState):
        if "topic" in state and state["topic"]:
            prompt="""
                You are an expert blog writer. Use Markdown formatting to create detailed blog content with expanded breakdown for this {topic}.
                """
            system_message = prompt.format(topic=state["topic"])
            response = self.llm.invoke(system_message)
            return {"blog":{"title":state['blog']['title'],"content":response.content}}
    
    def translation(self,state:BlogState):
        """
        Translating content to a chosen language"""

        translation_prompt=  """
        Translate content into the {current_language}.
        - Maintain the original tone, style and formatting.
        - Adapt cultural references that are appropiate for {current_language}.

        ORIGINAL CONTENT:
        {blog_content}
        """
        blog_content = state["blog"]["content"]
        messages=[
            HumanMessage(translation_prompt.format(current_language=state["current_language"],blog_content=blog_content))
        ]

        translation_content = self.llm.with_structured_output(Blog).invoke(messages)
        return {"blog": {"title": translation_content.title, "content": translation_content.content}}

    def route(self,state:BlogState):
        return {"current_language":state['current_language']}
    
    def route_decision(self,state:BlogState):

        if state["current_language"] == "hindi":
            return "hindi"
        elif state["current_language"] == "french":
            return "french"
        else:
            return state["current_language"]