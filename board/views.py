from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from .models import Question, Answer
from .forms import QuestionForm


def index(request):
    question_list = Question.objects.order_by('-create_date')
    context = {'question_list': question_list}
    return render(request, "board/question_list.html", context)


def detail(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    context = {'question': question}
    return render(request, 'board/question_detail.html', context)


def question_create(request):
    form = QuestionForm()
    return render(request, 'board/question_form.html', {'form' : form})


def answer_create(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    answer = Answer(question=question, content=request.POST.get('content'), create_date=timezone.now())
    answer.save()
    return redirect('board:detail', question_id=question.id)


def portfolio(request):
    return render(request, "portfolio.html")
