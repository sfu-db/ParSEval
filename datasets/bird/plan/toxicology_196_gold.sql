{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalSort",
      "sort": [
        {
          "column": 1,
          "type": "BIGINT"
        }
      ],
      "dir": [
        "DESCENDING"
      ],
      "offset": 0,
      "limit": 1,
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "VARCHAR"
            }
          ],
          "aggs": [
            {
              "operator": "COUNT",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [],
              "type": "BIGINT",
              "name": "EXPR$1"
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
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "VARCHAR"
                }
              ],
              "id": "1",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "bond",
                  "id": "0",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}