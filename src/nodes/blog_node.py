from src.states.blogstate import BlogState

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