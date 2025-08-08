{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 9,
  "limit": 2,
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "DIVIDE",
          "operator": "/",
          "type": "FLOAT",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 19,
                  "name": "$19",
                  "type": "FLOAT"
                }
              ]
            },
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "FLOAT",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 18,
                      "name": "$18",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "LITERAL",
                      "value": 0.0,
                      "type": "FLOAT",
                      "nullable": false,
                      "precision": 15
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "FLOAT",
                  "nullable": true,
                  "precision": 15
                },
                {
                  "kind": "INPUT_REF",
                  "index": 18,
                  "name": "$18",
                  "type": "FLOAT"
                }
              ]
            }
          ]
        },
        {
          "kind": "INPUT_REF",
          "index": 18,
          "name": "$18",
          "type": "FLOAT"
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "frpm",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}