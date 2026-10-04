import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0017_image_describe_prompt_yhct"),
    ]

    operations = [
        migrations.AddField(
            model_name="chunkingconfig",
            name="method",
            field=models.CharField(
                choices=[("llm", "LLM (cửa sổ trang)"), ("poincare", "Regex đề mục + Poincaré")],
                default="llm",
                help_text="LLM: gửi cửa sổ trang cho LLM. Poincaré: cắt đề mục regex rồi embed + Depth Score.",
                max_length=16,
                verbose_name="Phương pháp",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="embed_backend",
            field=models.CharField(
                choices=[("ollama", "Ollama"), ("vllm", "vLLM / OpenAI-compatible")],
                default="ollama",
                help_text="Chỉ dùng khi phương pháp là Poincaré.",
                max_length=16,
                verbose_name="Nguồn embedding",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="embed_host",
            field=models.CharField(
                blank=True,
                default="http://ai1.rnd.lcs.com.vn",
                help_text="Ollama: http://host:11434. vLLM: http://host:port/v1.",
                max_length=400,
                verbose_name="Host embedding",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="embed_model",
            field=models.CharField(
                blank=True,
                default="jina-embeddings-v4-text-retrieval-F16:latest",
                max_length=200,
                verbose_name="Model embedding",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="embed_api_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Tùy chọn — vLLM/OpenAI-compatible.",
                max_length=500,
                verbose_name="API key embedding",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="embed_batch_size",
            field=models.PositiveIntegerField(
                default=32,
                help_text="Số cửa sổ gửi embedding mỗi lần.",
                verbose_name="Batch embedding",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="poincare_window",
            field=models.PositiveIntegerField(
                default=20,
                help_text="Độ dài cửa sổ trượt (token regex).",
                verbose_name="Cửa sổ token",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="poincare_step",
            field=models.PositiveIntegerField(
                default=3,
                help_text="Số token dịch mỗi bước.",
                verbose_name="Bước trượt",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="poincare_c",
            field=models.FloatField(
                default=1.0,
                help_text="Độ cong đĩa Poincaré (phải > 0). Mặc định 1.",
                verbose_name="Độ cong c",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="smooth_s",
            field=models.PositiveIntegerField(
                default=2,
                help_text="Khoảng trống lân cận (số chẵn). Mặc định 2.",
                verbose_name="Làm mịn s",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="smooth_n",
            field=models.PositiveIntegerField(
                default=2,
                help_text="Số lần lặp làm mịn. Mặc định 2.",
                verbose_name="Làm mịn n",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="min_tokens",
            field=models.PositiveIntegerField(
                default=200,
                help_text="Gom các phần đề mục ngắn đến khi số token tiktoken vượt ngưỡng này.",
                verbose_name="Gom đến khi > token",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="max_tokens",
            field=models.PositiveIntegerField(
                default=200,
                help_text="Chỉ chạy Poincaré/Depth Score với đoạn đã gom dài hơn số này.",
                verbose_name="Cắt Poincaré khi > token",
            ),
        ),
        migrations.AlterField(
            model_name="chunkingconfig",
            name="provider",
            field=models.ForeignKey(
                blank=True,
                help_text="Chỉ dùng khi phương pháp là LLM. Để trống = LLM mặc định.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="chunking_configs",
                to="llm.llmprovider",
                verbose_name="LLM",
            ),
        ),
    ]
