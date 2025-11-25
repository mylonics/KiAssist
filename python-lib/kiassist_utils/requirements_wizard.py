"""Requirements wizard module for PCB project requirements generation."""

import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional


# Default wizard questions organized by category
DEFAULT_QUESTIONS = [
    {
        "id": "objectives",
        "category": "General",
        "question": "What are the general requirements and objectives of this PCB project?",
        "placeholder": "Describe what this board should accomplish...",
        "multiline": True
    },
    {
        "id": "known_parts",
        "category": "General",
        "question": "Are there any specific details or known parts that should be used?",
        "placeholder": "List any specific components, chips, or constraints...",
        "multiline": True
    },
    {
        "id": "mechanical",
        "category": "Mechanical",
        "question": "What are the mechanical requirements? (board size, mounting, enclosure constraints)",
        "placeholder": "e.g., Max 50x50mm, 4 corner mounting holes, must fit in specific enclosure...",
        "multiline": True
    },
    {
        "id": "power",
        "category": "Power",
        "question": "What are the power requirements? (input voltage, power consumption, battery)",
        "placeholder": "e.g., 5V USB input, expected 100mA average consumption, battery backup needed...",
        "multiline": True
    },
    {
        "id": "processing",
        "category": "Processing",
        "question": "What processing unit is needed and how should it be programmed?",
        "placeholder": "e.g., ARM Cortex-M4, programmed via SWD, needs to run FreeRTOS...",
        "multiline": True
    },
    {
        "id": "communication",
        "category": "Communication",
        "question": "What communication interfaces are required? (USB, UART, SPI, I2C, wireless)",
        "placeholder": "e.g., USB for programming, UART debug, I2C for sensors, WiFi/BLE...",
        "multiline": True
    },
    {
        "id": "sensors",
        "category": "Sensors",
        "question": "What sensors will the PCB need to interface with?",
        "placeholder": "e.g., Temperature sensor, accelerometer, ambient light sensor...",
        "multiline": True
    },
    {
        "id": "controls",
        "category": "Controls",
        "question": "What other controls or ICs are needed? (motor drivers, LEDs, displays)",
        "placeholder": "e.g., H-bridge for motor, RGB LEDs, OLED display...",
        "multiline": True
    },
    {
        "id": "analog",
        "category": "Analog",
        "question": "Are there any analog sensing or signal requirements?",
        "placeholder": "e.g., Analog input for potentiometer, audio input, current sensing...",
        "multiline": True
    }
]


def get_default_questions() -> List[Dict[str, Any]]:
    """Get the default wizard questions.
    
    Returns:
        List of question dictionaries
    """
    return DEFAULT_QUESTIONS.copy()


def check_requirements_file(project_dir: str) -> Dict[str, Any]:
    """Check if requirements.md exists in the project directory.
    
    Args:
        project_dir: Path to the project directory
        
    Returns:
        Dictionary with exists status and path information
    """
    try:
        project_path = Path(project_dir)
        if not project_path.exists():
            return {
                "success": False,
                "error": "Project directory does not exist"
            }
        
        requirements_path = project_path / "requirements.md"
        todo_path = project_path / "todo.md"
        
        return {
            "success": True,
            "requirements_exists": requirements_path.exists(),
            "requirements_path": str(requirements_path),
            "todo_exists": todo_path.exists(),
            "todo_path": str(todo_path)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def get_requirements_content(project_dir: str) -> Dict[str, Any]:
    """Read the contents of requirements.md from the project directory.
    
    Args:
        project_dir: Path to the project directory
        
    Returns:
        Dictionary with file contents or error
    """
    try:
        project_path = Path(project_dir)
        requirements_path = project_path / "requirements.md"
        
        if not requirements_path.exists():
            return {
                "success": False,
                "error": "requirements.md does not exist"
            }
        
        with open(requirements_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return {
            "success": True,
            "content": content
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def save_requirements_file(project_dir: str, requirements_content: str, 
                           todo_content: Optional[str] = None) -> Dict[str, Any]:
    """Save requirements.md and optionally todo.md to the project directory.
    
    Args:
        project_dir: Path to the project directory
        requirements_content: Content for requirements.md
        todo_content: Optional content for todo.md
        
    Returns:
        Dictionary with success status and saved paths
    """
    try:
        project_path = Path(project_dir)
        if not project_path.exists():
            return {
                "success": False,
                "error": "Project directory does not exist"
            }
        
        # Save requirements.md
        requirements_path = project_path / "requirements.md"
        with open(requirements_path, 'w', encoding='utf-8') as f:
            f.write(requirements_content)
        
        saved_files = [str(requirements_path)]
        
        # Save todo.md if provided
        if todo_content:
            todo_path = project_path / "todo.md"
            with open(todo_path, 'w', encoding='utf-8') as f:
                f.write(todo_content)
            saved_files.append(str(todo_path))
        
        return {
            "success": True,
            "saved_files": saved_files
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def get_agent_prompt() -> str:
    """Get the requirements agent prompt from the agent file.
    
    Returns:
        The agent prompt content
    """
    # Try to load from bundled location (for production)
    import sys
    if getattr(sys, 'frozen', False):
        base_path = Path(sys._MEIPASS)
        agent_path = base_path / "dist" / "agents" / "requirements-agent.md"
    else:
        # Development path
        current_file = Path(__file__)
        repo_root = current_file.parent.parent.parent
        agent_path = repo_root / "public" / "agents" / "requirements-agent.md"
    
    try:
        if agent_path.exists():
            with open(agent_path, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception:
        pass
    
    # Fallback inline prompt
    return """You are an assistant helping users define requirements for their PCB project.
When refining questions, analyze the initial answers and generate relevant follow-up questions.
Return refined questions as a JSON array.
When synthesizing requirements, create a requirements.md and todo.md document.
Return as JSON with 'requirements' and 'todo' fields containing the document content.
Be specific, technical, and avoid superfluous language."""


def build_refine_prompt(answers: Dict[str, str]) -> str:
    """Build the prompt for refining wizard questions.
    
    Args:
        answers: Dictionary of question_id to answer text for initial questions
        
    Returns:
        The prompt string to send to the LLM
    """
    agent_prompt = get_agent_prompt()
    
    # Format the initial answers
    answers_text = "\n".join([
        f"Q: {DEFAULT_QUESTIONS[0]['question'] if qid == 'objectives' else DEFAULT_QUESTIONS[1]['question']}\nA: {answer}"
        for qid, answer in answers.items()
        if qid in ('objectives', 'known_parts')
    ])
    
    prompt = f"""{agent_prompt}

---

The user has provided these initial answers about their PCB project:

{answers_text}

Based on these answers, please refine the remaining questions to be more specific to this project.
Return a JSON array of refined questions. Each question should have:
- id: unique identifier
- category: category name
- question: the question text
- placeholder: optional placeholder text
- multiline: boolean for multiline input

Focus on making questions specific to what the user described. Include all the standard categories (Mechanical, Power, Processing, Communication, Sensors, Controls, Analog) but tailor the questions to be relevant.
Respond with only the JSON array, no other text."""

    return prompt


def build_synthesize_prompt(questions: List[Dict[str, Any]], 
                           answers: Dict[str, str],
                           project_name: str = "PCB Project") -> str:
    """Build the prompt for synthesizing requirements documents.
    
    Args:
        questions: List of question dictionaries
        answers: Dictionary of question_id to answer text
        project_name: Name of the project
        
    Returns:
        The prompt string to send to the LLM
    """
    agent_prompt = get_agent_prompt()
    
    # Build Q&A list
    qa_text = ""
    for q in questions:
        qid = q['id']
        answer = answers.get(qid, "")
        if answer.strip():
            qa_text += f"Category: {q['category']}\n"
            qa_text += f"Q: {q['question']}\n"
            qa_text += f"A: {answer}\n\n"
    
    prompt = f"""{agent_prompt}

---

Project Name: {project_name}

Here are all the questions and answers from the requirements wizard:

{qa_text}

Please synthesize this information into two documents:
1. requirements.md - A professional technical requirements document
2. todo.md - A task list for implementing the project

Return a JSON object with exactly two keys:
- "requirements": the full content of requirements.md
- "todo": the full content of todo.md

The documents should:
- Use only standard ASCII characters (no emojis)
- Be succinct and technical
- Sound professional, not AI-generated
- Include specific measurable requirements where answers provide them

Respond with only the JSON object, no other text."""

    return prompt


def parse_refined_questions(llm_response: str) -> List[Dict[str, Any]]:
    """Parse the LLM response for refined questions.
    
    Args:
        llm_response: The raw response from the LLM
        
    Returns:
        List of question dictionaries, or default questions on parse failure
    """
    try:
        # Try to extract JSON from the response
        response = llm_response.strip()
        
        # Handle markdown code blocks
        if response.startswith("```"):
            lines = response.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```") and not in_json:
                    in_json = True
                    continue
                if line.startswith("```") and in_json:
                    break
                if in_json:
                    json_lines.append(line)
            response = "\n".join(json_lines)
        
        questions = json.loads(response)
        
        # Validate structure
        if isinstance(questions, list):
            valid_questions = []
            for q in questions:
                if isinstance(q, dict) and 'id' in q and 'question' in q:
                    valid_questions.append({
                        'id': q.get('id'),
                        'category': q.get('category', 'General'),
                        'question': q.get('question'),
                        'placeholder': q.get('placeholder', ''),
                        'multiline': q.get('multiline', True)
                    })
            if valid_questions:
                return valid_questions
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    
    # Return remaining default questions on failure
    return DEFAULT_QUESTIONS[2:]  # Skip first two (objectives and known_parts)


def parse_synthesized_docs(llm_response: str) -> Dict[str, str]:
    """Parse the LLM response for synthesized documents.
    
    Args:
        llm_response: The raw response from the LLM
        
    Returns:
        Dictionary with 'requirements' and 'todo' content
    """
    try:
        response = llm_response.strip()
        
        # Handle markdown code blocks
        if response.startswith("```"):
            lines = response.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```") and not in_json:
                    in_json = True
                    continue
                if line.startswith("```") and in_json:
                    break
                if in_json:
                    json_lines.append(line)
            response = "\n".join(json_lines)
        
        docs = json.loads(response)
        
        if isinstance(docs, dict):
            return {
                'requirements': docs.get('requirements', ''),
                'todo': docs.get('todo', '')
            }
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    
    # Return empty on failure
    return {'requirements': '', 'todo': ''}
