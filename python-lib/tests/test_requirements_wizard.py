"""Tests for requirements wizard module."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from kiassist_utils.requirements_wizard import (
    get_default_questions,
    check_requirements_file,
    get_requirements_content,
    save_requirements_file,
    build_refine_prompt,
    build_synthesize_prompt,
    parse_refined_questions,
    parse_synthesized_docs,
    DEFAULT_QUESTIONS,
)


class TestGetDefaultQuestions:
    """Tests for get_default_questions function."""

    def test_returns_list(self):
        """Test that it returns a list."""
        questions = get_default_questions()
        assert isinstance(questions, list)

    def test_returns_copy(self):
        """Test that it returns a copy, not the original."""
        questions = get_default_questions()
        questions.append({'id': 'test'})
        assert len(get_default_questions()) == len(DEFAULT_QUESTIONS)

    def test_questions_have_required_fields(self):
        """Test that all questions have required fields."""
        questions = get_default_questions()
        for q in questions:
            assert 'id' in q
            assert 'category' in q
            assert 'question' in q

    def test_includes_all_categories(self):
        """Test that questions cover all expected categories."""
        questions = get_default_questions()
        categories = {q['category'] for q in questions}
        expected = {'General', 'Mechanical', 'Power', 'Processing', 
                   'Communication', 'Sensors', 'Controls', 'Analog'}
        assert expected.issubset(categories)


class TestCheckRequirementsFile:
    """Tests for check_requirements_file function."""

    def test_nonexistent_directory(self):
        """Test checking a nonexistent directory."""
        result = check_requirements_file('/nonexistent/path')
        assert result['success'] is False

    def test_empty_directory(self):
        """Test checking an empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_requirements_file(tmpdir)
            assert result['success'] is True
            assert result['requirements_exists'] is False
            assert result['todo_exists'] is False

    def test_directory_with_requirements(self):
        """Test checking a directory with requirements.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            req_file = Path(tmpdir) / 'requirements.md'
            req_file.write_text('# Requirements\n')
            
            result = check_requirements_file(tmpdir)
            assert result['success'] is True
            assert result['requirements_exists'] is True

    def test_directory_with_both_files(self):
        """Test checking a directory with both requirements.md and todo.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'requirements.md').write_text('# Requirements\n')
            (Path(tmpdir) / 'todo.md').write_text('# TODO\n')
            
            result = check_requirements_file(tmpdir)
            assert result['success'] is True
            assert result['requirements_exists'] is True
            assert result['todo_exists'] is True


class TestGetRequirementsContent:
    """Tests for get_requirements_content function."""

    def test_nonexistent_file(self):
        """Test reading from nonexistent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_requirements_content(tmpdir)
            assert result['success'] is False

    def test_reads_content(self):
        """Test reading content from existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            content = '# Project Requirements\n\nTest content.'
            (Path(tmpdir) / 'requirements.md').write_text(content)
            
            result = get_requirements_content(tmpdir)
            assert result['success'] is True
            assert result['content'] == content


class TestSaveRequirementsFile:
    """Tests for save_requirements_file function."""

    def test_nonexistent_directory(self):
        """Test saving to nonexistent directory."""
        result = save_requirements_file('/nonexistent/path', 'content')
        assert result['success'] is False

    def test_saves_requirements_only(self):
        """Test saving only requirements.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            content = '# Requirements\n\nTest.'
            result = save_requirements_file(tmpdir, content)
            
            assert result['success'] is True
            assert len(result['saved_files']) == 1
            
            saved_content = (Path(tmpdir) / 'requirements.md').read_text()
            assert saved_content == content

    def test_saves_both_files(self):
        """Test saving both requirements.md and todo.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            req_content = '# Requirements'
            todo_content = '# TODO'
            
            result = save_requirements_file(tmpdir, req_content, todo_content)
            
            assert result['success'] is True
            assert len(result['saved_files']) == 2
            
            assert (Path(tmpdir) / 'requirements.md').read_text() == req_content
            assert (Path(tmpdir) / 'todo.md').read_text() == todo_content


class TestBuildRefinePrompt:
    """Tests for build_refine_prompt function."""

    def test_includes_answers(self):
        """Test that prompt includes provided answers."""
        answers = {
            'objectives': 'Build a motor controller',
            'known_parts': 'Using STM32F4'
        }
        
        prompt = build_refine_prompt(answers)
        
        assert 'motor controller' in prompt
        assert 'STM32F4' in prompt

    def test_includes_agent_prompt(self):
        """Test that prompt includes agent guidance."""
        prompt = build_refine_prompt({})
        
        # Should include instruction about JSON
        assert 'JSON' in prompt


class TestBuildSynthesizePrompt:
    """Tests for build_synthesize_prompt function."""

    def test_includes_project_name(self):
        """Test that prompt includes project name."""
        prompt = build_synthesize_prompt([], {}, 'MyProject')
        assert 'MyProject' in prompt

    def test_includes_questions_and_answers(self):
        """Test that prompt includes Q&A."""
        questions = [
            {'id': 'test1', 'category': 'General', 'question': 'What is the goal?'}
        ]
        answers = {'test1': 'Build a sensor board'}
        
        prompt = build_synthesize_prompt(questions, answers)
        
        assert 'What is the goal?' in prompt
        assert 'sensor board' in prompt


class TestParseRefinedQuestions:
    """Tests for parse_refined_questions function."""

    def test_parses_valid_json(self):
        """Test parsing valid JSON response."""
        response = json.dumps([
            {'id': 'q1', 'category': 'Test', 'question': 'Test question?'}
        ])
        
        result = parse_refined_questions(response)
        
        assert len(result) == 1
        assert result[0]['id'] == 'q1'

    def test_parses_json_in_code_block(self):
        """Test parsing JSON wrapped in markdown code block."""
        response = '''```json
[{"id": "q1", "category": "Test", "question": "Test?"}]
```'''
        
        result = parse_refined_questions(response)
        
        assert len(result) == 1

    def test_returns_defaults_on_invalid_json(self):
        """Test returning default questions on invalid JSON."""
        result = parse_refined_questions('invalid json')
        
        # Should return remaining default questions (skipping first 2)
        assert len(result) == len(DEFAULT_QUESTIONS) - 2


class TestParseSynthesizedDocs:
    """Tests for parse_synthesized_docs function."""

    def test_parses_valid_json(self):
        """Test parsing valid JSON response."""
        response = json.dumps({
            'requirements': '# Requirements',
            'todo': '# TODO'
        })
        
        result = parse_synthesized_docs(response)
        
        assert result['requirements'] == '# Requirements'
        assert result['todo'] == '# TODO'

    def test_parses_json_in_code_block(self):
        """Test parsing JSON wrapped in markdown code block."""
        response = '''```json
{"requirements": "# Req", "todo": "# Tasks"}
```'''
        
        result = parse_synthesized_docs(response)
        
        assert result['requirements'] == '# Req'
        assert result['todo'] == '# Tasks'

    def test_returns_empty_on_invalid_json(self):
        """Test returning empty on invalid JSON."""
        result = parse_synthesized_docs('not json')
        
        assert result['requirements'] == ''
        assert result['todo'] == ''
