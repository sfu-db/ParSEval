{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "MAX",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "FLOAT"
        }
      ],
      "type": "FLOAT",
      "name": "EXPR$0"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "FLOAT"
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "budget",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}