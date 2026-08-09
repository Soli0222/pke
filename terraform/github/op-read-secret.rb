#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "open3"
require "shellwords"

def fail_with(message)
  warn(message)
  exit 1
end

def run_command(*command)
  stdout, stderr, status = Open3.capture3(*command)
  return stdout if status.success?

  fail_with("#{command.shelljoin} failed: #{stderr.strip}")
end

def field_value(item, field, vault)
  command = ["op", "item", "get", item, "--fields", "label=#{field}", "--format", "json"]
  command += ["--vault", vault] if vault && !vault.empty?

  parsed = JSON.parse(run_command(*command))
  if parsed.is_a?(Array)
    match = parsed.find { |entry| entry["label"] == field || entry["id"] == field }
    return match["value"] if match && match.key?("value")
  elsif parsed.is_a?(Hash)
    return parsed["value"] if parsed.key?("value")
  end

  fail_with("field #{field.inspect} was not found in 1Password item #{item.inspect}")
end

def reference_value(reference)
  run_command("op", "read", reference)
end

def secret_value(source, source_key)
  unknown_keys = source.keys - %w[reference vault item field file]
  fail_with("unknown onepassword keys for source #{source_key}: #{unknown_keys.join(", ")}") unless unknown_keys.empty?

  reference = source["reference"]
  item = source["item"]
  field = source["field"]
  file = source["file"]
  vault = source["vault"]

  if reference && !reference.empty?
    reference_value(reference)
  elsif item && field && !item.empty? && !field.empty?
    field_value(item, field, vault)
  elsif item && file && vault && !item.empty? && !file.empty? && !vault.empty?
    reference_value("op://#{vault}/#{item}/#{file}")
  else
    fail_with("onepassword source #{source_key} requires either reference, item+field, or vault+item+file")
  end
end

query = JSON.parse($stdin.read)
unknown_query_keys = query.keys - %w[sources]
fail_with("unknown query keys: #{unknown_query_keys.join(", ")}") unless unknown_query_keys.empty?

sources = JSON.parse(query.fetch("sources"))
fail_with("sources must be a JSON object") unless sources.is_a?(Hash)

values = sources.sort.each_with_object({}) do |(source_key, source), result|
  fail_with("onepassword source #{source_key} must be a JSON object") unless source.is_a?(Hash)

  result[source_key] = secret_value(source, source_key)
end

print JSON.generate(values)
